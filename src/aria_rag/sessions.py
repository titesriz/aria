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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from aria_rag.config import ROOT_DIR
from aria_rag.retriever import SearchHit, format_page_citation

logger = logging.getLogger(__name__)

SESSIONS_DIR = ROOT_DIR / "data" / "sessions"
# Single shared file, unlike the per-process session_*.jsonl logs — feedback
# is low-volume and there's no benefit to splitting it per server process.
FEEDBACK_LOG_PATH = SESSIONS_DIR / "feedback.jsonl"


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
    synthesis_model_was_resident: bool | None,
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
        # True: synthesis_model was already resident before this call (no
        # load/swap). False: it wasn't, and synthesis_ms includes a load.
        # None: residency couldn't be determined (check itself failed).
        # Explains the ~20s latency variance from the split-model swap cost
        # (gemma3 expansion / ministral synthesis don't co-reside in 8GB).
        "synthesis_model_was_resident": synthesis_model_was_resident,
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
    synthesis_model_was_resident: bool | None = None,
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
            synthesis_model_was_resident=synthesis_model_was_resident,
            error=error,
            latency_ms=latency_ms,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — logging must never break the answer path
        logger.error("Session log write failed (%s): %s", log_path, exc)


def read_all_entries(sessions_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load every /ask entry across all session_*.jsonl files, oldest first.

    Excludes feedback.jsonl (a different schema — human corrections, not
    /ask calls). Malformed lines are skipped rather than raising, since this
    reads logs written by past and possibly differently-versioned processes.
    """
    sessions_dir = sessions_dir or SESSIONS_DIR
    entries: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.glob("session_*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries


def format_entry_digest(entry: dict[str, Any]) -> str:
    """Human-readable digest of one /ask session entry for `aria-rag sessions`."""
    lines = [
        f"Q: {entry.get('question')}",
        f"timestamp: {entry.get('timestamp')}",
    ]

    expansion_status = entry.get("expansion_status")
    if entry.get("expand_query_requested"):
        lines.append(f"expansion: {expansion_status} -> {entry.get('expansion_query')}")
    else:
        lines.append("expansion: not requested")

    hits = entry.get("hits") or []
    lines.append(f"hits ({len(hits)}):")
    for h in hits:
        lines.append(
            f"  [{h.get('rank')}] {h.get('filename')}"
            f" | section={h.get('section')}"
            f" | {h.get('page_citation')}"
            f" | score={h.get('score')} faiss={h.get('faiss_score')} bm25={h.get('bm25_score')}"
        )

    answer = entry.get("answer") or ""
    lines.append(f"answer (first 200 chars): {answer[:200]!r}")

    synthesis_model = entry.get("synthesis_model")
    was_resident = entry.get("synthesis_model_was_resident")
    if synthesis_model is not None or was_resident is not None:
        lines.append(f"synthesis_model: {synthesis_model} (was_resident={was_resident})")
    else:
        lines.append("synthesis_model: <field absent — pre-dates synthesis_model logging>")

    latency = entry.get("latency_ms") or {}
    latency_str = ", ".join(f"{k}={v}ms" for k, v in latency.items())
    lines.append(f"latency: {latency_str}")

    if entry.get("error"):
        lines.append(f"error: {entry['error']}")

    return "\n".join(lines)


def log_feedback(
    *,
    session_id: str,
    question: str,
    answer_shown: str,
    expected_answer: str,
    expected_documents: str,
    log_path: Path | None = None,
) -> None:
    """Append one human-feedback line. Never raises — same guarantee as
    log_ask_call, for the same reason: the /feedback endpoint must return
    202 regardless of whether the write actually landed.
    """
    log_path = log_path or FEEDBACK_LOG_PATH
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "question": question,
            "answer_shown": answer_shown,
            "expected_answer": expected_answer,
            "expected_documents": expected_documents,
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — logging must never break the endpoint response
        logger.error("Feedback log write failed (%s): %s", log_path, exc)
