"""Tests for the PoC validation instrument's comparison/calibration logic
(src/aria_rag/eval.py, 2026-09-22) — no live LLM calls; these test the pure
data-shaping functions, not judge_answer itself (see tests/test_judge.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.eval import (
    CALIBRATION_CASE_IDS,
    INDICATIVE_ONLY_CASE_IDS,
    _failed_forbidden_checks,
    _mean,
    build_comparison_rows,
    export_calibration_sheet,
    load_baseline_answers_from_file,
)


def _judge(checks, substance_score=0.8, final_score=0.7, error=False):
    if error:
        return {"judge_error": True, "final_score": None, "raw_responses": []}
    return {"checks": checks, "substance_score": substance_score, "final_score": final_score,
            "rationale": "r", "judge_error": False}


def _aria_row(uc_id, question="q", answer_score=0.8, judge=None):
    return {"id": uc_id, "question": question, "retrieval_score": 1.0,
            "answer_score": answer_score, "judge": judge, "raw_answer": "first system's answer text"}


def _base_row(uc_id, question="q", answer_score=0.4, judge=None):
    return {"id": uc_id, "question": question, "answer_score": answer_score,
            "judge": judge, "raw_answer": "second system's answer text"}


# ---------------------------------------------------------------------------
# _mean
# ---------------------------------------------------------------------------

def test_mean_ignores_none():
    assert _mean([0.5, None, 1.0]) == 0.75


def test_mean_all_none_returns_none():
    assert _mean([None, None]) is None


def test_mean_empty_returns_none():
    assert _mean([]) is None


# ---------------------------------------------------------------------------
# _failed_forbidden_checks
# ---------------------------------------------------------------------------

def test_failed_forbidden_checks_extracts_only_failed_forbidden():
    judge = _judge([
        {"type": "required", "pass": False, "check": "req"},
        {"type": "forbidden", "pass": False, "check": "no albedo"},
        {"type": "forbidden", "pass": True, "check": "no hallucination"},
    ])
    assert _failed_forbidden_checks(judge) == ["no albedo"]


def test_failed_forbidden_checks_empty_on_judge_error():
    assert _failed_forbidden_checks(_judge([], error=True)) == []


def test_failed_forbidden_checks_empty_on_none():
    assert _failed_forbidden_checks(None) == []


# ---------------------------------------------------------------------------
# build_comparison_rows
# ---------------------------------------------------------------------------

def test_build_comparison_rows_computes_delta():
    aria = [_aria_row("UC-01", answer_score=0.9)]
    base = [_base_row("UC-01", answer_score=0.3)]
    rows = build_comparison_rows(aria, base)
    assert len(rows) == 1
    row = rows[0]
    assert row["aria_answer_score"] == 0.9
    assert row["baseline_answer_score"] == 0.3
    assert row["delta"] == pytest.approx(0.6)


def test_build_comparison_rows_flags_indicative_only_cases():
    aria = [_aria_row("CH-04"), _aria_row("UC-01")]
    base = [_base_row("CH-04"), _base_row("UC-01")]
    rows = build_comparison_rows(aria, base)
    by_id = {r["id"]: r for r in rows}
    assert by_id["CH-04"]["indicative_only"] is True
    assert by_id["UC-01"]["indicative_only"] is False
    assert INDICATIVE_ONLY_CASE_IDS == {"CH-04", "UC-16", "UC-04"}


def test_build_comparison_rows_delta_none_when_either_score_missing():
    aria = [_aria_row("UC-01", answer_score=None)]
    base = [_base_row("UC-01", answer_score=0.3)]
    rows = build_comparison_rows(aria, base)
    assert rows[0]["delta"] is None


def test_build_comparison_rows_missing_baseline_case_handled():
    """A case present in ARIA's results but absent from the baseline's
    (e.g. a baseline run scoped to fewer --ids) must not crash."""
    aria = [_aria_row("UC-01")]
    rows = build_comparison_rows(aria, baseline_results=[])
    assert rows[0]["baseline_answer_score"] is None
    assert rows[0]["delta"] is None


def test_build_comparison_rows_preserves_aria_result_order():
    aria = [_aria_row("UC-05"), _aria_row("CH-01"), _aria_row("UC-01")]
    base = [_base_row("UC-01"), _base_row("CH-01"), _base_row("UC-05")]
    rows = build_comparison_rows(aria, base)
    assert [r["id"] for r in rows] == ["UC-05", "CH-01", "UC-01"]


# ---------------------------------------------------------------------------
# load_baseline_answers_from_file
# ---------------------------------------------------------------------------

def test_load_baseline_answers_from_file(tmp_path):
    p = tmp_path / "answers.json"
    p.write_text(json.dumps({"CH-01": "answer text", "UC-01": "another"}), encoding="utf-8")
    result = load_baseline_answers_from_file(p)
    assert result == {"CH-01": "answer text", "UC-01": "another"}


def test_load_baseline_answers_from_file_rejects_non_dict(tmp_path):
    p = tmp_path / "answers.json"
    p.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline_answers_from_file(p)


# ---------------------------------------------------------------------------
# export_calibration_sheet — blind A/B, reproducible randomization
# ---------------------------------------------------------------------------

def test_calibration_sheet_never_names_system_in_sheet_text(tmp_path):
    judge = _judge([{"type": "required", "pass": True, "check": "c", "justification": "j"}])
    aria = [_aria_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    base = [_base_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    sheet_path, key_path = export_calibration_sheet(aria, base, tmp_path)

    sheet_text = sheet_path.read_text(encoding="utf-8").lower()
    assert "aria" not in sheet_text
    assert "claude" not in sheet_text
    assert "baseline" not in sheet_text


def test_calibration_key_records_true_assignment(tmp_path):
    judge = _judge([{"type": "required", "pass": True, "check": "c", "justification": "j"}])
    aria = [_aria_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    base = [_base_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    sheet_path, key_path = export_calibration_sheet(aria, base, tmp_path)

    key = json.loads(key_path.read_text(encoding="utf-8"))
    assert set(key.keys()) == set(CALIBRATION_CASE_IDS)
    for uc_id, mapping in key.items():
        assert {mapping["A"], mapping["B"]} == {"aria", "baseline_no_corpus"}


def test_calibration_sheet_covers_exactly_the_four_named_cases(tmp_path):
    assert CALIBRATION_CASE_IDS == ["CH-03", "CH-01", "UC-02", "UC-05"]
    judge = _judge([{"type": "required", "pass": True, "check": "c", "justification": "j"}])
    aria = [_aria_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    base = [_base_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    _, key_path = export_calibration_sheet(aria, base, tmp_path)
    key = json.loads(key_path.read_text(encoding="utf-8"))
    assert sorted(key.keys()) == sorted(CALIBRATION_CASE_IDS)


def test_calibration_sheet_randomization_reproducible_with_same_seed(tmp_path):
    judge = _judge([{"type": "required", "pass": True, "check": "c", "justification": "j"}])
    aria = [_aria_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    base = [_base_row(cid, judge=judge) for cid in CALIBRATION_CASE_IDS]
    _, key_path_1 = export_calibration_sheet(aria, base, tmp_path / "run1", seed=42)
    _, key_path_2 = export_calibration_sheet(aria, base, tmp_path / "run2", seed=42)
    assert json.loads(key_path_1.read_text(encoding="utf-8")) == json.loads(key_path_2.read_text(encoding="utf-8"))
