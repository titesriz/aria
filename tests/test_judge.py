"""Unit tests for the LLM-as-judge (src/aria_rag/judge.py). No live Ollama
calls -- judge_answer's HTTP call is mocked throughout."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag import judge as judge_module
from aria_rag.config import Settings
from aria_rag.judge import (
    _extract_json,
    _validate_judge_json,
    compute_final_score,
    judge_answer,
)


# ---------------------------------------------------------------------------
# compute_final_score — the deterministic formula
# ---------------------------------------------------------------------------

def test_score_all_required_pass_full_substance():
    checks = [{"type": "required", "pass": True}, {"type": "required", "pass": True}]
    assert compute_final_score(checks, substance_score=1.0) == 1.0


def test_score_one_required_fails_halves_that_component():
    checks = [{"type": "required", "pass": True}, {"type": "required", "pass": False}]
    # required_pass_rate=0.5, substance=1.0 -> 0.5*0.5 + 0.5*1.0 = 0.75
    assert compute_final_score(checks, substance_score=1.0) == 0.75


def test_score_forbidden_violation_caps_at_point_three_even_if_otherwise_perfect():
    checks = [
        {"type": "required", "pass": True},
        {"type": "forbidden", "pass": False},  # did the forbidden thing
    ]
    assert compute_final_score(checks, substance_score=1.0) == 0.3


def test_score_forbidden_respected_no_cap():
    checks = [{"type": "required", "pass": True}, {"type": "forbidden", "pass": True}]
    assert compute_final_score(checks, substance_score=1.0) == 1.0


def test_score_no_required_checks_defaults_to_vacuous_pass():
    checks = [{"type": "forbidden", "pass": True}]
    # required_pass_rate=1.0 (vacuous), substance=0.6 -> 0.5*1.0 + 0.5*0.6 = 0.8
    assert compute_final_score(checks, substance_score=0.6) == 0.8


def test_score_low_base_not_further_reduced_by_cap_if_already_below():
    # base already below the 0.3 cap -- min() must not accidentally raise it
    checks = [{"type": "required", "pass": False}, {"type": "forbidden", "pass": False}]
    # required_pass_rate=0.0, substance=0.0 -> base=0.0; forbidden violated -> min(0.0, 0.3) = 0.0
    assert compute_final_score(checks, substance_score=0.0) == 0.0


# ---------------------------------------------------------------------------
# _extract_json — robust extraction from raw model output
# ---------------------------------------------------------------------------

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_strips_surrounding_prose():
    raw = 'Voici le résultat :\n{"a": 1}\nFin.'
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("ceci n'est pas du JSON") is None


def test_extract_json_returns_none_on_malformed_braces():
    assert _extract_json('{"a": 1,}') is None


# ---------------------------------------------------------------------------
# _validate_judge_json — schema validation
# ---------------------------------------------------------------------------

def test_validate_accepts_well_formed():
    data = {
        "checks": [{"check": "x", "type": "required", "pass": True, "justification": "y"}],
        "substance_score": 0.5,
    }
    assert _validate_judge_json(data) is True


def test_validate_rejects_missing_checks():
    assert _validate_judge_json({"substance_score": 0.5}) is False


def test_validate_rejects_bad_check_type():
    data = {"checks": [{"check": "x", "type": "maybe", "pass": True, "justification": "y"}], "substance_score": 0.5}
    assert _validate_judge_json(data) is False


def test_validate_rejects_non_bool_pass():
    data = {"checks": [{"check": "x", "type": "required", "pass": "yes", "justification": "y"}], "substance_score": 0.5}
    assert _validate_judge_json(data) is False


def test_validate_rejects_substance_score_out_of_range():
    data = {"checks": [], "substance_score": 1.5}
    assert _validate_judge_json(data) is False


def test_validate_accepts_empty_checks_list():
    assert _validate_judge_json({"checks": [], "substance_score": 0.5}) is True


# ---------------------------------------------------------------------------
# judge_answer — end to end, HTTP mocked
# ---------------------------------------------------------------------------

def _settings() -> Settings:
    return Settings(synthesis_model="ministral-3:8b", ollama_host="http://localhost:11434", num_ctx=8192)


def _fake_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"response": text}
    return resp


def test_judge_answer_success_first_try():
    good = (
        '{"checks": [{"check": "cite UG.3.2", "type": "required", "pass": true, '
        '"justification": "la réponse cite UG.3.2"}], "substance_score": 0.9, '
        '"rationale": "accord sur le fond"}'
    )
    with patch.object(judge_module.httpx, "post", return_value=_fake_response(good)) as mock_post:
        result = judge_answer("q?", "réponse", "attendue", "critère", _settings())

    assert mock_post.call_count == 1
    assert result["judge_error"] is False
    assert result["final_score"] == compute_final_score(result["checks"], result["substance_score"])


def test_judge_answer_retries_once_then_succeeds():
    bad = "pas du JSON du tout"
    good = '{"checks": [], "substance_score": 0.7, "rationale": "ok"}'
    with patch.object(judge_module.httpx, "post", side_effect=[_fake_response(bad), _fake_response(good)]) as mock_post:
        result = judge_answer("q?", "réponse", "attendue", "critère", _settings())

    assert mock_post.call_count == 2
    assert result["judge_error"] is False
    assert result["final_score"] == 0.85  # no checks -> vacuous pass 1.0, substance 0.7 -> 0.5+0.35


def test_judge_answer_records_judge_error_after_two_failures():
    bad = "toujours pas du JSON"
    with patch.object(judge_module.httpx, "post", return_value=_fake_response(bad)) as mock_post:
        result = judge_answer("q?", "réponse", "attendue", "critère", _settings())

    assert mock_post.call_count == 2
    assert result["judge_error"] is True
    assert result["final_score"] is None
    assert len(result["raw_responses"]) == 2


def test_judge_answer_sends_temperature_zero_and_seed():
    good = '{"checks": [], "substance_score": 0.5, "rationale": "ok"}'
    with patch.object(judge_module.httpx, "post", return_value=_fake_response(good)) as mock_post:
        judge_answer("q?", "réponse", "attendue", "critère", _settings())

    options = mock_post.call_args.kwargs["json"]["options"]
    assert options["temperature"] == 0
    assert options["seed"] == 42


def test_judge_answer_prompt_never_names_a_system():
    """Blind-judge contract: the system prompt must never mention ARIA or
    Claude by name, so it can't bias the verdict toward either."""
    lowered = judge_module.JUDGE_SYSTEM_PROMPT.lower()
    assert "aria" not in lowered
    assert "claude" not in lowered
