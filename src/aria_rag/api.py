from __future__ import annotations

import logging
import re
import textwrap
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import faiss
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from aria_rag.backend_check import BackendStatus, is_model_resident, startup_should_refuse, verify_backend
from aria_rag.config import Settings, load_settings
from aria_rag.indexer import Chunk
from aria_rag.llm import answer_question
from aria_rag.retriever import (
    SearchHit,
    _search_weighted_within,
    _search_within,
    format_page_citation,
    load_index,
    scoped_retrieval_merge,
)
from aria_rag.sessions import log_ask_call, new_session_log_path

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    print(f"[models] expansion={settings.expansion_model}  synthesis={settings.synthesis_model}", flush=True)

    backend_status = verify_backend(settings)
    app.state.backend_status = backend_status
    if backend_status.checked:
        for check, stage in (
            (backend_status.expansion_check, "expansion"),
            (backend_status.synthesis_check, "synthesis"),
        ):
            tag = "\033[92mGPU\033[0m" if check.gpu_verified else "\033[91mCPU/UNVERIFIED\033[0m"
            print(f"[backend check] {stage} model {check.model}: {tag}", flush=True)

    if startup_should_refuse(settings.llm_backend, backend_status, settings.allow_cpu):
        message = (
            "\033[91mBACKEND CHECK FAILED: at least one Ollama model did not land 100% on GPU "
            "(silent CPU fallback). This has confounded measurements twice before — refusing to "
            "start. Fix the Ollama/Vulkan setup, or pass --allow-cpu to override.\033[0m"
        )
        print(message, flush=True)
        logger.error("Backend check failed: refusing to start (CPU fallback detected)")
        raise RuntimeError("Backend GPU check failed — refusing to start. Use --allow-cpu to override.")
    elif backend_status.checked and not backend_status.gpu_verified and settings.allow_cpu:
        print(
            "\033[91mWARNING: CPU fallback detected, but --allow-cpu is set — starting anyway. "
            "Expect much higher latency than the certified GPU baseline.\033[0m",
            flush=True,
        )

    model, faiss_index, bm25, chunks = load_index(settings)
    app.state.settings = settings
    app.state.model = model
    app.state.faiss_index = faiss_index
    app.state.bm25 = bm25
    app.state.chunks = chunks
    app.state.session_log_path = new_session_log_path()
    logger.info("Model loaded, ready to serve")
    print("Model loaded, ready to serve", flush=True)
    logger.info(
        "Resolved models: expansion=%s synthesis=%s",
        settings.expansion_model, settings.synthesis_model,
    )
    print(f"Session log: {app.state.session_log_path}", flush=True)
    yield


app = FastAPI(title="ARIA RAG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    expand_query: bool = False
    backend: Optional[str] = None


class Citation(BaseModel):
    source: str
    full_path: str
    family: str
    excerpt: str
    page: Optional[int] = None
    page_end: Optional[int] = None
    page_citation: Optional[str] = None
    section: Optional[str] = None


def _strip_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'^[ \t]*[*\-][ \t]+', '• ', text, flags=re.MULTILINE)
    # Safety net: replace any Windows absolute path with just the filename
    text = re.sub(r'[A-Za-z]:\\(?:[^\\<>:"/|?*\n]+\\)+([^\\<>:"/|?*\n]+)', r'\1', text)
    return text


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]


def _search(
    settings: Settings,
    model: SentenceTransformer,
    faiss_index: faiss.Index,
    bm25: object,
    chunks: list[Chunk],
    query: str,
    top_k: int | None = None,
    family_filter: list[str] | None = None,
    scoped: bool | None = None,
) -> list[SearchHit]:
    """Same family_filter / scoped retrieval semantics as retriever.search()."""
    limit = top_k or settings.top_k
    use_scoped = settings.scoped_retrieval if scoped is None else scoped

    if family_filter:
        allowed = {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
        return _search_within(model, faiss_index, bm25, chunks, query, limit, allowed, boost_factor=settings.lexical_boost_factor)

    if not use_scoped:
        allowed = set(range(len(chunks)))
        return _search_within(model, faiss_index, bm25, chunks, query, limit, allowed, boost_factor=settings.lexical_boost_factor)

    present_families = {c.doc_family for c in chunks}
    scopes = [f for f in settings.family_slots if f in present_families]
    if not scopes:
        allowed = set(range(len(chunks)))
        return _search_within(model, faiss_index, bm25, chunks, query, limit, allowed, boost_factor=settings.lexical_boost_factor)

    def fetch_fn(family: str, k: int) -> list[SearchHit]:
        fam_allowed = {i for i, c in enumerate(chunks) if c.doc_family == family}
        return _search_within(model, faiss_index, bm25, chunks, query, k, fam_allowed, boost_factor=settings.lexical_boost_factor)

    return scoped_retrieval_merge(scopes, settings.family_slots, limit, fetch_fn)


def _search_weighted(
    settings: Settings,
    model: SentenceTransformer,
    faiss_index: faiss.Index,
    bm25: object,
    chunks: list[Chunk],
    query_original: str,
    query_expansion: str | None,
    top_k: int | None = None,
    alpha: float = 0.5,
    family_filter: list[str] | None = None,
    scoped: bool | None = None,
) -> list[SearchHit]:
    """Same family_filter / scoped retrieval semantics as retriever.search_weighted()."""
    limit = top_k or settings.top_k
    use_scoped = settings.scoped_retrieval if scoped is None else scoped

    if family_filter:
        allowed = {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            limit, allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    if not use_scoped:
        allowed = set(range(len(chunks)))
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            limit, allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    present_families = {c.doc_family for c in chunks}
    scopes = [f for f in settings.family_slots if f in present_families]
    if not scopes:
        allowed = set(range(len(chunks)))
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            limit, allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    def fetch_fn(family: str, k: int) -> list[SearchHit]:
        fam_allowed = {i for i, c in enumerate(chunks) if c.doc_family == family}
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            k, fam_allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    return scoped_retrieval_merge(scopes, settings.family_slots, limit, fetch_fn)


def _health_payload(state) -> dict:
    bs: BackendStatus | None = getattr(state, "backend_status", None)
    return {
        "status": "ok",
        "backend_status": {
            "expansion_model": bs.expansion_model,
            "synthesis_model": bs.synthesis_model,
            "gpu_verified": bs.gpu_verified,
            "last_check": bs.last_check,
        } if bs is not None else None,
    }


@app.get("/health")
def health() -> dict:
    return _health_payload(app.state)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    state = app.state
    settings: Settings = state.settings
    backend = req.backend or settings.llm_backend
    synthesis_model = {
        "ollama": settings.synthesis_model,
        "openai": settings.chat_model,
        "claude": settings.claude_model,
    }.get(backend)

    timestamp = datetime.now(timezone.utc).isoformat()
    total_t0 = time.monotonic()
    latency_ms: dict[str, float] = {}
    expansion_status: str | None = None
    expansion_query_text = ""
    hits: list[SearchHit] = []
    answer_text: str | None = None
    error_message: str | None = None
    synthesis_model_was_resident: bool | None = None

    try:
        try:
            if req.expand_query:
                from aria_rag.query_expansion import DEFAULT_EXPANSION_CACHE_PATH, expand_query
                t0 = time.monotonic()
                original_q, expansion_query_text, _, expansion_status = expand_query(
                    req.question,
                    backend=backend,
                    ollama_host=settings.ollama_host,
                    ollama_model=settings.expansion_model,
                    cache_path=DEFAULT_EXPANSION_CACHE_PATH,
                )
                latency_ms["expansion_ms"] = round((time.monotonic() - t0) * 1000, 1)

                t0 = time.monotonic()
                hits = _search_weighted(
                    settings, state.model, state.faiss_index, state.bm25, state.chunks,
                    query_original=original_q,
                    query_expansion=expansion_query_text,
                    alpha=0.5,
                )
            else:
                t0 = time.monotonic()
                hits = _search(
                    settings, state.model, state.faiss_index, state.bm25, state.chunks,
                    query=req.question,
                )
            latency_ms["retrieval_ms"] = round((time.monotonic() - t0) * 1000, 1)
        except Exception as exc:  # noqa: BLE001 — never leak a raw stack trace to the client
            logger.exception("Retrieval failed for question=%r", req.question)
            error_message = "La recherche dans le corpus a échoué. Merci de réessayer."
            raise HTTPException(status_code=503, detail=error_message) from exc

        if not hits:
            error_message = "Aucun passage pertinent n'a été trouvé pour cette question."
            raise HTTPException(status_code=404, detail=error_message)

        synthesis_model_was_resident = (
            is_model_resident(settings.ollama_host, settings.synthesis_model) if backend == "ollama" else None
        )
        try:
            t0 = time.monotonic()
            answer_text = answer_question(req.question, hits, settings, backend)
            latency_ms["synthesis_ms"] = round((time.monotonic() - t0) * 1000, 1)
        except RuntimeError as exc:
            logger.exception("Synthesis failed for question=%r", req.question)
            error_message = "La génération de la réponse a échoué. Merci de réessayer."
            raise HTTPException(status_code=502, detail=error_message) from exc
        except Exception as exc:  # noqa: BLE001 — same guarantee for anything unforeseen
            logger.exception("Unexpected error during synthesis for question=%r", req.question)
            error_message = "Une erreur inattendue est survenue. Merci de réessayer."
            raise HTTPException(status_code=500, detail=error_message) from exc

        citations = [
            Citation(
                source=Path(h.source_path).name,
                full_path=h.source_path,
                family=h.doc_family,
                excerpt=textwrap.shorten(h.content, width=500, placeholder="..."),
                page=h.page,
                page_end=h.page_end,
                page_citation=format_page_citation(h.page, h.page_end),
                section=h.section,
            )
            for h in hits
        ]
        return AskResponse(answer=_strip_markdown(answer_text), citations=citations)
    finally:
        latency_ms["total_ms"] = round((time.monotonic() - total_t0) * 1000, 1)
        log_ask_call(
            state.session_log_path,
            timestamp=timestamp,
            question=req.question,
            expand_query_requested=req.expand_query,
            expansion_status=expansion_status,
            expansion_query=expansion_query_text,
            hits=hits,
            answer=answer_text,
            synthesis_model=synthesis_model,
            synthesis_model_was_resident=synthesis_model_was_resident,
            error=error_message,
            latency_ms=latency_ms,
        )


def serve() -> None:
    import uvicorn
    uvicorn.run("aria_rag.api:app", host="0.0.0.0", port=8000, reload=False)
