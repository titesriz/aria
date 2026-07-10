"""Per-request session logging for the demo API (aria_rag.api).

Append-only JSONL, one line per /ask call — written for the human retest
so every question, expansion, retrieved hits, answer, and latency is
reconstructable afterward without relying on server console scrollback.

log_ask_call() must never raise: a logging failure is a server-side
concern (logged via `logger`), never a reason to fail the actual answer.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from aria_rag.config import ROOT_DIR
from aria_rag.retriever import SearchHit, format_page_citation

logger = logging.getLogger(__name__)

SESSIONS_DIR = ROOT_DIR / "data" / "sessions"


def new_session_log_path(sessions_dir: Path | None = None) -> Path:
    """One path per server process — every /ask call during this process's
    lifetime appends to the same file (created lazily on first write).
    """
    sessions_dir = sessions_dir or SESSIONS_DIR
    return sessions_dir / f"session_{date.today().isoformat()}_{uuid.uuid4().hex[:8]}.jsonl"


def _build_entry(
    *,
    timestamp: str,
    question: str,
    expand_query_requested: bool,
    expansion_status: str | None,
    expansion_query: str,
    hits: list[SearchHit],
    answer: str | None,
    synthesis_model: str | None,
    error: str | None,
    latency_ms: dict[str, float],
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "question": question,
        "expand_query_requested": expand_query_requested,
        "expansion_status": expansion_status,
        "expansion_query": expansion_query or None,
        "hits": [
            {
                "rank": i + 1,
                "filename": Path(h.source_path).name,
                "section": h.section,
                "page_citation": format_page_citation(h.page, h.page_end),
                "score": round(h.score, 5) if h.score is not None else None,
                "faiss_score": round(h.faiss_score, 5) if h.faiss_score is not None else None,
                "bm25_score": round(h.bm25_score, 5) if h.bm25_score is not None else None,
            }
            for i, h in enumerate(hits)
        ],
        "answer": answer,
        "synthesis_model": synthesis_model,
        "error": error,
        "latency_ms": latency_ms,
    }


def log_ask_call(
    log_path: Path,
    *,
    timestamp: str,
    question: str,
    expand_query_requested: bool,
    expansion_status: str | None,
    expansion_query: str,
    hits: list[SearchHit],
    answer: str | None,
    synthesis_model: str | None = None,
    error: str | None,
    latency_ms: dict[str, float],
) -> None:
    """Append one JSON line to log_path. Never raises."""
    try:
        entry = _build_entry(
            timestamp=timestamp,
            question=question,
            expand_query_requested=expand_query_requested,
            expansion_status=expansion_status,
            expansion_query=expansion_query,
            hits=hits,
            answer=answer,
            synthesis_model=synthesis_model,
            error=error,
            latency_ms=latency_ms,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — logging must never break the answer path
        logger.error("Session log write failed (%s): %s", log_path, exc)
