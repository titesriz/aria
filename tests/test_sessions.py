"""Regression tests for the demo API's session log (src/aria_rag/sessions.py).

Written for the human retest: every /ask call must produce one JSONL line
with enough detail to reconstruct what happened (question, expansion,
hits, answer, latency) without server console scrollback — and a logging
failure must never be capable of breaking the actual answer response.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.retriever import SearchHit
from aria_rag.sessions import log_ask_call, log_feedback, new_session_log_path


def _hit(**overrides) -> SearchHit:
    defaults = dict(
        source_path="/docs/REG1.pdf",
        doc_family="reglement_ecrit",
        score=0.5,
        content="UG.1.1 some content",
        page=19,
        page_end=21,
        section="UG.1.1",
        faiss_score=0.04,
        bm25_score=12.3,
    )
    defaults.update(overrides)
    return SearchHit(**defaults)


# ---------------------------------------------------------------------------
# new_session_log_path
# ---------------------------------------------------------------------------

def test_new_session_log_path_matches_pattern(tmp_path):
    path = new_session_log_path(tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".jsonl"
    assert path.name.startswith("session_")
    parts = path.stem.split("_")
    # session_{YYYY-MM-DD}_{uuid8} -> ["session", "YYYY-MM-DD", "uuid8"]
    assert len(parts[-1]) == 8


def test_new_session_log_path_is_unique_per_call(tmp_path):
    assert new_session_log_path(tmp_path) != new_session_log_path(tmp_path)


# ---------------------------------------------------------------------------
# log_ask_call — shape and content
# ---------------------------------------------------------------------------

def test_log_ask_call_writes_one_jsonl_line_with_expected_fields(tmp_path):
    log_path = tmp_path / "session_test.jsonl"
    log_ask_call(
        log_path,
        timestamp="2026-07-10T12:00:00+00:00",
        question="Quelle hauteur maximale ?",
        expand_query_requested=True,
        expansion_status="ok",
        expansion_query="UG.3.2",
        hits=[_hit()],
        answer="La hauteur maximale est...",
        error=None,
        latency_ms={"expansion_ms": 8000.0, "retrieval_ms": 50.0, "synthesis_ms": 900.0, "total_ms": 8950.0},
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])

    assert entry["question"] == "Quelle hauteur maximale ?"
    assert entry["expand_query_requested"] is True
    assert entry["expansion_status"] == "ok"
    assert entry["expansion_query"] == "UG.3.2"
    assert entry["answer"] == "La hauteur maximale est..."
    assert entry["error"] is None
    assert entry["latency_ms"]["total_ms"] == 8950.0

    assert len(entry["hits"]) == 1
    hit = entry["hits"][0]
    assert hit["rank"] == 1
    assert hit["filename"] == "REG1.pdf"
    assert hit["section"] == "UG.1.1"
    assert hit["page_citation"] == "p. 19–21"
    assert hit["score"] == 0.5


def test_log_ask_call_appends_multiple_lines(tmp_path):
    log_path = tmp_path / "session_test.jsonl"
    for i in range(3):
        log_ask_call(
            log_path, timestamp=f"t{i}", question=f"q{i}", expand_query_requested=False,
            expansion_status=None, expansion_query="", hits=[], answer="a", error=None,
            latency_ms={"total_ms": 1.0},
        )
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert [json.loads(l)["question"] for l in lines] == ["q0", "q1", "q2"]


def test_log_ask_call_records_error_and_none_answer(tmp_path):
    log_path = tmp_path / "session_test.jsonl"
    log_ask_call(
        log_path, timestamp="t", question="q", expand_query_requested=False,
        expansion_status=None, expansion_query="", hits=[], answer=None,
        error="La génération de la réponse a échoué. Merci de réessayer.",
        latency_ms={"total_ms": 1.0},
    )
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["answer"] is None
    assert "échoué" in entry["error"]


# ---------------------------------------------------------------------------
# Failure-safety — must never raise
# ---------------------------------------------------------------------------

def test_log_ask_call_never_raises_when_path_is_unwritable(tmp_path):
    """log_path pointing at a directory (not a file) can never be opened for
    append — this must be swallowed, not propagated, since the whole point
    is that a logging failure can never break the answer path.
    """
    bad_path = tmp_path / "a_directory"
    bad_path.mkdir()
    log_ask_call(
        bad_path, timestamp="t", question="q", expand_query_requested=False,
        expansion_status=None, expansion_query="", hits=[], answer="a", error=None,
        latency_ms={"total_ms": 1.0},
    )  # must not raise


# ---------------------------------------------------------------------------
# log_feedback — shape, content, and failure-safety
# ---------------------------------------------------------------------------

def test_log_feedback_writes_one_jsonl_line_with_expected_fields(tmp_path):
    log_path = tmp_path / "feedback.jsonl"
    log_feedback(
        log_path=log_path,
        session_id="session_2026-07-11_abc12345",
        question="Quelle hauteur maximale ?",
        answer_shown="La hauteur maximale est...",
        expected_answer="Le contexte ne précise pas...",
        expected_documents="REG1_MS1.pdf",
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])

    assert entry["session_id"] == "session_2026-07-11_abc12345"
    assert entry["question"] == "Quelle hauteur maximale ?"
    assert entry["answer_shown"] == "La hauteur maximale est..."
    assert entry["expected_answer"] == "Le contexte ne précise pas..."
    assert entry["expected_documents"] == "REG1_MS1.pdf"
    assert "timestamp" in entry


def test_log_feedback_appends_multiple_lines(tmp_path):
    log_path = tmp_path / "feedback.jsonl"
    for i in range(3):
        log_feedback(
            log_path=log_path, session_id=f"s{i}", question=f"q{i}",
            answer_shown="a", expected_answer="", expected_documents="",
        )
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert [json.loads(l)["question"] for l in lines] == ["q0", "q1", "q2"]


def test_log_feedback_never_raises_when_path_is_unwritable(tmp_path):
    bad_path = tmp_path / "a_directory"
    bad_path.mkdir()
    log_feedback(
        log_path=bad_path, session_id="s", question="q",
        answer_shown="a", expected_answer="", expected_documents="",
    )  # must not raise
