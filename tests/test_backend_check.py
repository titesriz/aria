"""Regression tests for the Ollama backend health check (src/aria_rag/backend_check.py).

Written after CPU fallback silently confounded measurements twice — a
model landing on CPU still answers requests, just far slower, with no
exception raised. These tests simulate that exact failure mode (a
/api/ps response where size_vram < size, or the load call itself
failing) and assert it's detected loudly rather than swallowed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from aria_rag import backend_check
from aria_rag.backend_check import (
    check_model_gpu,
    check_model_gpu_with_retry,
    is_model_resident,
    resolve_git_commit,
    startup_should_refuse,
    verify_backend,
)
from aria_rag.config import Settings


def _response(json_body, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_body
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = RuntimeError("http error")
    return resp


def _settings(**overrides) -> Settings:
    base = dict(
        llm_backend="ollama",
        expansion_model="gemma3:4b",
        synthesis_model="ministral-3:8b",
        ollama_host="http://localhost:11434",
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# check_model_gpu
# ---------------------------------------------------------------------------

def test_check_model_gpu_verified_when_fully_vram_resident():
    ps_response = _response({"models": [
        {"model": "gemma3:4b", "size": 1000, "size_vram": 1000},
    ]})
    with patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        result = check_model_gpu("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is True
    assert result.size == 1000
    assert result.size_vram == 1000
    assert result.error is None


def test_check_model_gpu_not_verified_on_partial_cpu_fallback():
    """The exact failure mode that bit twice: model loaded and answering,
    but only partially (or not at all) VRAM-resident.
    """
    ps_response = _response({"models": [
        {"model": "gemma3:4b", "size": 3000000000, "size_vram": 400000000},
    ]})
    with patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        result = check_model_gpu("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is False
    assert result.size == 3000000000
    assert result.size_vram == 400000000


def test_check_model_gpu_not_verified_when_size_vram_zero():
    """Pure CPU fallback: size_vram absent/0 while size is the full model."""
    ps_response = _response({"models": [
        {"model": "gemma3:4b", "size": 3000000000, "size_vram": 0},
    ]})
    with patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        result = check_model_gpu("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is False


def test_check_model_gpu_load_failure_reported_not_raised():
    with patch.object(backend_check.httpx, "post", side_effect=backend_check.httpx.HTTPError("refused")):
        result = check_model_gpu("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is False
    assert "load failed" in result.error


def test_check_model_gpu_model_missing_from_ps_after_load():
    ps_response = _response({"models": []})
    with patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        result = check_model_gpu("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is False
    assert "not found" in result.error


def test_check_model_gpu_matches_on_name_field_too():
    """Ollama's /api/ps has used both "model" and "name" keys across
    versions — match on either.
    """
    ps_response = _response({"models": [
        {"name": "gemma3:4b", "size": 500, "size_vram": 500},
    ]})
    with patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        result = check_model_gpu("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is True


# ---------------------------------------------------------------------------
# check_model_gpu_with_retry — the cold-boot race (Ollama not listening yet)
# ---------------------------------------------------------------------------

def test_retry_succeeds_after_connection_refused_then_up():
    """The exact failure mode reported this week: connection refused on
    the first model, success on the second seconds later. Simulates two
    refused attempts followed by Ollama coming up.
    """
    ps_response = _response({"models": [{"model": "gemma3:4b", "size": 100, "size_vram": 100}]})
    post_calls = {"n": 0}

    def fake_post(*args, **kwargs):
        post_calls["n"] += 1
        if post_calls["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return _response({})

    with patch.object(backend_check, "time") as mock_time, \
         patch.object(backend_check.httpx, "post", side_effect=fake_post), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        result = check_model_gpu_with_retry("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is True
    assert post_calls["n"] == 3
    # Two retries happened (attempts 1 and 2 failed), so two backoff sleeps,
    # the documented 2s/4s doubling.
    assert mock_time.sleep.call_args_list == [((2.0,),), ((4.0,),)]


def test_retry_fails_clearly_after_exhausting_attempts():
    """A genuinely-down Ollama must still fail clearly, not hang forever —
    all 5 attempts refused, bounded wait, clear error at the end.
    """
    with patch.object(backend_check, "time") as mock_time, \
         patch.object(backend_check.httpx, "post", side_effect=httpx.ConnectError("connection refused")):
        result = check_model_gpu_with_retry("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is False
    assert "after 5 attempts" in result.error
    assert mock_time.sleep.call_count == 4  # 4 backoff waits between 5 attempts


def test_retry_does_not_retry_genuine_cpu_fallback():
    """A model that Ollama reaches but reports as CPU-resident is a real,
    distinct failure — must be reported on the first attempt, never
    retried into a 30s wait.
    """
    ps_response = _response({"models": [{"model": "gemma3:4b", "size": 3000000000, "size_vram": 0}]})
    with patch.object(backend_check, "time") as mock_time, \
         patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        result = check_model_gpu_with_retry("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is False
    mock_time.sleep.assert_not_called()


def test_check_model_gpu_single_attempt_reports_connection_failure_immediately():
    """check_model_gpu (as opposed to the _with_retry variant) never
    retries — used where Ollama being down is a fact to report, not a
    race to wait out.
    """
    with patch.object(backend_check, "time") as mock_time, \
         patch.object(backend_check.httpx, "post", side_effect=httpx.ConnectError("connection refused")):
        result = check_model_gpu("http://localhost:11434", "gemma3:4b")

    assert result.gpu_verified is False
    assert "load failed" in result.error
    mock_time.sleep.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_git_commit — version visibility (the ghost-server killer)
# ---------------------------------------------------------------------------

def test_resolve_git_commit_returns_hash_on_success(tmp_path):
    fake_result = MagicMock(returncode=0, stdout="a1b2c3d\n")
    with patch.object(backend_check.subprocess, "run", return_value=fake_result):
        assert resolve_git_commit(tmp_path) == "a1b2c3d"


def test_resolve_git_commit_unknown_on_nonzero_exit(tmp_path):
    fake_result = MagicMock(returncode=128, stdout="")
    with patch.object(backend_check.subprocess, "run", return_value=fake_result):
        assert resolve_git_commit(tmp_path) == "unknown"


def test_resolve_git_commit_unknown_when_git_missing(tmp_path):
    with patch.object(backend_check.subprocess, "run", side_effect=FileNotFoundError("git not found")):
        assert resolve_git_commit(tmp_path) == "unknown"


def test_resolve_git_commit_unknown_on_subprocess_error(tmp_path):
    with patch.object(backend_check.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)):
        assert resolve_git_commit(tmp_path) == "unknown"


# ---------------------------------------------------------------------------
# verify_backend
# ---------------------------------------------------------------------------

def test_verify_backend_both_gpu_verified():
    ps_response = _response({"models": [
        {"model": "gemma3:4b", "size": 100, "size_vram": 100},
        {"model": "ministral-3:8b", "size": 200, "size_vram": 200},
    ]})
    with patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", return_value=ps_response):
        status = verify_backend(_settings())

    assert status.gpu_verified is True
    assert status.checked is True
    assert status.expansion_model == "gemma3:4b"
    assert status.synthesis_model == "ministral-3:8b"


def test_verify_backend_false_when_either_model_on_cpu():
    """One model landing on CPU is enough to fail the overall check —
    both stages get exercised on every request in production."""
    calls = {"n": 0}

    def fake_ps(*args, **kwargs):
        calls["n"] += 1
        # First check (expansion) -> GPU; second (synthesis) -> CPU.
        if calls["n"] == 1:
            return _response({"models": [{"model": "gemma3:4b", "size": 100, "size_vram": 100}]})
        return _response({"models": [{"model": "ministral-3:8b", "size": 100, "size_vram": 10}]})

    with patch.object(backend_check.httpx, "post", return_value=_response({})), \
         patch.object(backend_check.httpx, "get", side_effect=fake_ps):
        status = verify_backend(_settings())

    assert status.gpu_verified is False
    assert status.expansion_check.gpu_verified is True
    assert status.synthesis_check.gpu_verified is False


def test_verify_backend_skips_check_for_non_ollama_backend():
    with patch.object(backend_check.httpx, "post") as mock_post, \
         patch.object(backend_check.httpx, "get") as mock_get:
        status = verify_backend(_settings(llm_backend="openai"))

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    assert status.checked is False
    assert status.gpu_verified is True  # vacuous pass — nothing to verify


# ---------------------------------------------------------------------------
# startup_should_refuse — pure decision logic
# ---------------------------------------------------------------------------

def _status(gpu_verified: bool, checked: bool = True) -> backend_check.BackendStatus:
    return backend_check.BackendStatus(
        expansion_model="gemma3:4b", synthesis_model="ministral-3:8b",
        gpu_verified=gpu_verified, last_check="t", checked=checked,
    )


def test_startup_refuses_on_cpu_fallback_by_default():
    assert startup_should_refuse("ollama", _status(gpu_verified=False), allow_cpu=False) is True


def test_startup_does_not_refuse_when_allow_cpu_set():
    assert startup_should_refuse("ollama", _status(gpu_verified=False), allow_cpu=True) is False


def test_startup_does_not_refuse_when_gpu_verified():
    assert startup_should_refuse("ollama", _status(gpu_verified=True), allow_cpu=False) is False


def test_startup_does_not_refuse_for_non_ollama_backend():
    assert startup_should_refuse("openai", _status(gpu_verified=False), allow_cpu=False) is False


# ---------------------------------------------------------------------------
# is_model_resident
# ---------------------------------------------------------------------------

def test_is_model_resident_true_when_listed():
    ps_response = _response({"models": [{"model": "ministral-3:8b", "size": 1, "size_vram": 1}]})
    with patch.object(backend_check.httpx, "get", return_value=ps_response):
        assert is_model_resident("http://localhost:11434", "ministral-3:8b") is True


def test_is_model_resident_false_when_not_listed():
    ps_response = _response({"models": [{"model": "gemma3:4b", "size": 1, "size_vram": 1}]})
    with patch.object(backend_check.httpx, "get", return_value=ps_response):
        assert is_model_resident("http://localhost:11434", "ministral-3:8b") is False


def test_is_model_resident_none_when_check_fails():
    with patch.object(backend_check.httpx, "get", side_effect=backend_check.httpx.HTTPError("down")):
        assert is_model_resident("http://localhost:11434", "ministral-3:8b") is None


# ---------------------------------------------------------------------------
# /health payload shape (src/aria_rag/api.py's _health_payload)
# ---------------------------------------------------------------------------

def test_health_payload_shape_with_backend_status():
    from aria_rag.api import _health_payload
    from aria_rag.backend_check import BackendStatus

    class FakeState:
        backend_status = BackendStatus(
            expansion_model="gemma3:4b", synthesis_model="ministral-3:8b",
            gpu_verified=True, last_check="2026-07-10T00:00:00+00:00",
        )
        git_commit = "a1b2c3d"

    payload = _health_payload(FakeState())
    assert payload["status"] == "ok"
    assert payload["git_commit"] == "a1b2c3d"
    assert payload["backend_status"] == {
        "expansion_model": "gemma3:4b",
        "synthesis_model": "ministral-3:8b",
        "gpu_verified": True,
        "last_check": "2026-07-10T00:00:00+00:00",
    }


def test_health_payload_backend_status_none_before_startup_check_ran():
    from aria_rag.api import _health_payload

    class FakeState:
        pass  # no backend_status or git_commit attribute set yet

    payload = _health_payload(FakeState())
    assert payload["status"] == "ok"
    assert payload["backend_status"] is None
    assert payload["git_commit"] == "unknown"
