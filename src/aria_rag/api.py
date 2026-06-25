from __future__ import annotations

import logging
import re
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import faiss
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from aria_rag.config import Settings, load_settings
from aria_rag.indexer import Chunk
from aria_rag.llm import answer_question
from aria_rag.retriever import RRF_K, SearchHit, _rrf_scores, _tokenize, load_index

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    model, faiss_index, bm25, chunks = load_index(settings)
    app.state.settings = settings
    app.state.model = model
    app.state.faiss_index = faiss_index
    app.state.bm25 = bm25
    app.state.chunks = chunks
    logger.info("Model loaded, ready to serve")
    print("Model loaded, ready to serve", flush=True)
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
) -> list[SearchHit]:
    limit = top_k or settings.top_k
    allowed = (
        {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
        if family_filter
        else set(range(len(chunks)))
    )
    if not allowed:
        return []
    fetch_k = min(limit * 10, len(allowed))
    rrf = _rrf_scores(query, model, faiss_index, bm25, chunks, allowed, fetch_k)
    top_indices = sorted(rrf, key=rrf.__getitem__, reverse=True)[:limit]
    return [
        SearchHit(
            source_path=chunks[i].source_path,
            doc_family=chunks[i].doc_family,
            score=rrf[i],
            content=chunks[i].content,
        )
        for i in top_indices
    ]


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
) -> list[SearchHit]:
    if not query_expansion or not query_expansion.strip():
        return _search(settings, model, faiss_index, bm25, chunks, query_original, top_k, family_filter)

    limit = top_k or settings.top_k
    allowed = (
        {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
        if family_filter
        else set(range(len(chunks)))
    )
    if not allowed:
        return []
    fetch_k = min(limit * 10, len(allowed))

    rrf_orig = _rrf_scores(query_original, model, faiss_index, bm25, chunks, allowed, fetch_k)
    try:
        rrf_exp = _rrf_scores(query_expansion, model, faiss_index, bm25, chunks, allowed, fetch_k)
    except Exception as exc:
        logger.warning("search_weighted: expansion retrieval failed (%s), falling back to original", exc)
        top_indices = sorted(rrf_orig, key=rrf_orig.__getitem__, reverse=True)[:limit]
        return [
            SearchHit(
                source_path=chunks[i].source_path,
                doc_family=chunks[i].doc_family,
                score=rrf_orig[i],
                content=chunks[i].content,
            )
            for i in top_indices
        ]

    all_ids = set(rrf_orig) | set(rrf_exp)
    combined = {
        i: alpha * rrf_orig.get(i, 0.0) + (1 - alpha) * rrf_exp.get(i, 0.0)
        for i in all_ids
    }
    top_indices = sorted(combined, key=combined.__getitem__, reverse=True)[:limit]
    return [
        SearchHit(
            source_path=chunks[i].source_path,
            doc_family=chunks[i].doc_family,
            score=combined[i],
            content=chunks[i].content,
        )
        for i in top_indices
    ]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    state = app.state
    settings: Settings = state.settings
    backend = req.backend or settings.llm_backend

    if req.expand_query:
        from aria_rag.query_expansion import expand_query
        original_q, expansion_q, _ = expand_query(
            req.question,
            backend=backend,
            ollama_host=settings.ollama_host,
            ollama_model=settings.ollama_model,
        )
        hits = _search_weighted(
            settings, state.model, state.faiss_index, state.bm25, state.chunks,
            query_original=original_q,
            query_expansion=expansion_q,
            alpha=0.5,
        )
    else:
        hits = _search(
            settings, state.model, state.faiss_index, state.bm25, state.chunks,
            query=req.question,
        )

    if not hits:
        raise HTTPException(status_code=404, detail="No relevant passages found.")

    try:
        answer = answer_question(req.question, hits, settings, backend)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    citations = [
        Citation(
            source=Path(h.source_path).name,
            full_path=h.source_path,
            family=h.doc_family,
            excerpt=textwrap.shorten(h.content, width=500, placeholder="..."),
        )
        for h in hits
    ]

    return AskResponse(answer=_strip_markdown(answer), citations=citations)


def serve() -> None:
    import uvicorn
    uvicorn.run("aria_rag.api:app", host="0.0.0.0", port=8000, reload=False)
