"""Regression tests for loud expansion failures (src/aria_rag/query_expansion.py,
eval.py) and collision-proof eval result filenames (eval.py).

Before this fix, an Ollama exception/timeout during query expansion was
swallowed and returned as an empty article list — indistinguishable from a
genuine "no relevant articles" answer, and a source of unexplained variance
in the expanded eval metric (see the run-to-run investigation this task
followed up on). expansion_status ("ok" | "empty" | "failed") makes that
distinction explicit and propagates it through the cache, the CLI debug
output, and the eval result JSON.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag import query_expansion
from aria_rag.eval import _make_results_path, _parse_output, _print_summary
from aria_rag.query_expansion import _cache_key, _expand_with_ollama, expand_query

_QUESTION = "Je souhaite remplacer mon toit en zinc par un toit en tuile."


# ---------------------------------------------------------------------------
# _expand_with_ollama — exception path
# ---------------------------------------------------------------------------

def test_expand_with_ollama_exception_returns_failed_status(caplog):
    with patch.object(query_expansion.httpx, "post", side_effect=RuntimeError("connection refused")):
        with caplog.at_level(logging.ERROR, logger="aria_rag.query_expansion"):
            articles, status = _expand_with_ollama(_QUESTION, "http://localhost:11434", "gemma3:4b")

    assert articles == []
    assert status == "failed"
    assert any(
        record.levelno == logging.ERROR and _QUESTION in record.getMessage()
        for record in caplog.records
    ), "expected an ERROR-level log naming the question on expansion failure"


# ---------------------------------------------------------------------------
# expand_query — status propagation + caching
# ---------------------------------------------------------------------------

def test_expand_query_propagates_failed_status_and_caches_it(tmp_path):
    cache_path = tmp_path / "expansion_cache.json"
    with patch.object(query_expansion.httpx, "post", side_effect=RuntimeError("timeout")):
        _, expansion_q, articles, status = expand_query(
            _QUESTION, cache_path=cache_path,
        )

    assert status == "failed"
    assert articles == []
    assert expansion_q == ""

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache[query_expansion._cache_key(_QUESTION)]["expansion_status"] == "failed"


def test_expand_query_cache_hit_returns_cached_status(tmp_path):
    cache_path = tmp_path / "expansion_cache.json"
    cache_path.write_text(
        json.dumps({_QUESTION: {
            "expansion_query": "", "inferred_articles": [], "expansion_status": "failed",
        }}),
        encoding="utf-8",
    )
    # No mock needed — a cache hit must never call out to Ollama.
    _, expansion_q, articles, status = expand_query(_QUESTION, cache_path=cache_path)

    assert status == "failed"
    assert articles == []
    assert expansion_q == ""


def test_expand_query_cache_hit_backward_compat_infers_status(tmp_path):
    """Cache entries written before this fix have no expansion_status key —
    must not crash, and must infer a reasonable status from inferred_articles
    rather than requiring the whole cache to be invalidated.
    """
    cache_path = tmp_path / "expansion_cache.json"
    cache_path.write_text(
        json.dumps({
            "q_with_articles": {"expansion_query": "UG.2.2.3", "inferred_articles": ["UG.2.2.3"]},
            "q_empty": {"expansion_query": "", "inferred_articles": []},
        }),
        encoding="utf-8",
    )
    _, _, _, status_with_articles = expand_query("q_with_articles", cache_path=cache_path)
    _, _, _, status_empty = expand_query("q_empty", cache_path=cache_path)

    assert status_with_articles == "ok"
    assert status_empty == "empty"


# ---------------------------------------------------------------------------
# _cache_key — normalized cache key (interactive /ask reuse)
# ---------------------------------------------------------------------------

def test_cache_key_collapses_whitespace_and_casefolds():
    assert _cache_key("  Quelle   hauteur  ?  ") == "quelle hauteur ?"
    assert _cache_key("Quelle hauteur ?") == _cache_key("  Quelle   hauteur  ?  ")
    assert _cache_key("QUELLE HAUTEUR ?") == _cache_key("quelle hauteur ?")


def test_expand_query_cache_hit_across_whitespace_and_case_variation(tmp_path):
    """A question typed interactively with different spacing/casing than
    what's cached must still hit — this is the whole point of normalizing
    the key, since eval's cache is keyed by exact golden-dataset text but
    /ask now shares the same cache file for interactive questions.
    """
    cache_path = tmp_path / "expansion_cache.json"
    cache_path.write_text(
        json.dumps({_cache_key(_QUESTION): {
            "expansion_query": "UG.2.2.3", "inferred_articles": ["UG.2.2.3"], "expansion_status": "ok",
        }}),
        encoding="utf-8",
    )
    variant = "  " + _QUESTION.upper() + "  "
    # No mock — must be a cache hit, never a live Ollama call.
    original_q, expansion_q, articles, status = expand_query(variant, cache_path=cache_path)

    assert status == "ok"
    assert articles == ["UG.2.2.3"]
    assert original_q == variant  # the caller's exact text is preserved, only the cache KEY is normalized


def test_load_cache_matches_pre_normalization_raw_keyed_entries(tmp_path):
    """A cache file written before normalization existed (raw question text
    as key, e.g. with irregular whitespace) must still be matched.
    """
    cache_path = tmp_path / "expansion_cache.json"
    raw_key = "  " + _QUESTION + "   "  # simulates an old entry keyed by unnormalized text
    cache_path.write_text(
        json.dumps({raw_key: {"expansion_query": "X.1", "inferred_articles": ["X.1"], "expansion_status": "ok"}}),
        encoding="utf-8",
    )
    _, _, articles, status = expand_query(_QUESTION, cache_path=cache_path)
    assert status == "ok"
    assert articles == ["X.1"]


# ---------------------------------------------------------------------------
# eval.py — debug-output parsing
# ---------------------------------------------------------------------------

def test_parse_output_extracts_expansion_status_failed():
    raw = (
        "[query expansion] status : failed\n"
        "[query expansion] WARNING: expansion failed — falling back to original query\n\n"
        "Retrieved passages:\n"
    )
    _, _, _, _, expansion_status, _ = _parse_output(raw)
    assert expansion_status == "failed"


def test_parse_output_extracts_expansion_status_ok():
    raw = (
        "[query expansion] status : ok\n"
        "[query expansion] articles inférés : ['UG.2.2.3']\n"
        "[query expansion] expansion query  : UG.2.2.3\n\n"
        "Retrieved passages:\n"
    )
    _, _, expansion_query, inferred_articles, expansion_status, _ = _parse_output(raw)
    assert expansion_status == "ok"
    assert inferred_articles == ["UG.2.2.3"]
    assert expansion_query == "UG.2.2.3"


def test_parse_output_expansion_status_none_when_expansion_not_requested():
    raw = "Retrieved passages:\n"
    _, _, _, _, expansion_status, _ = _parse_output(raw)
    assert expansion_status is None


# ---------------------------------------------------------------------------
# eval.py — prominent warning on a failed case
# ---------------------------------------------------------------------------

def _result(uc_id: str, expansion_status: str | None) -> dict:
    return {
        "id": uc_id,
        "question": "some question",
        "retrieval_score": 0.5,
        "answer_score": 0.0,
        "missing_articles": [],
        "missing_keywords": [],
        "expansion_status": expansion_status,
        "error": None,
    }


def test_print_summary_warns_and_marks_failed_case(capsys):
    results = [_result("CH-01", "ok"), _result("CH-03", "failed")]
    _print_summary(results)
    out = capsys.readouterr().out

    assert "QUERY EXPANSION FAILED" in out
    assert "CH-03" in out
    assert "expansion failed" in out


def test_print_summary_no_warning_when_all_ok():
    results = [_result("CH-01", "ok"), _result("UC-02", None)]
    _print_summary(results)  # must not raise; no assertion on stdout needed here


# ---------------------------------------------------------------------------
# eval.py — collision-proof result filenames
# ---------------------------------------------------------------------------

def test_make_results_path_same_second_yields_distinct_files(tmp_path):
    """Two writes with the identical timestamp (simulating concurrent or
    fast-sequential eval runs landing in the same wall-clock second) must
    never collide.
    """
    p1 = _make_results_path(tmp_path, ts="20260101_000000")
    p2 = _make_results_path(tmp_path, ts="20260101_000000")

    assert p1 != p2
    assert p1.name.startswith("results_20260101_000000_")
    assert p2.name.startswith("results_20260101_000000_")

    p1.write_text("{}", encoding="utf-8")
    p2.write_text("{}", encoding="utf-8")
    assert p1.read_text(encoding="utf-8") == "{}"
    assert p2.read_text(encoding="utf-8") == "{}"
