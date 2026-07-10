"""Regression tests for the Ollama backend health check (src/aria_rag/backend_check.py).

Written after CPU fallback silently confounded measurements twice — a
model landing on CPU still answers requests, just far slower, with no
exception raised. These tests simulate that exact failure mode (a
/api/ps response where size_vram < size, or the load call itself
failing) and assert it's detected loudly rather than swallowed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag import backend_check
from aria_rag.backend_check import (
    check_model_gpu,
    is_model_resident,
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

    payload = _health_payload(FakeState())
    assert payload["status"] == "ok"
    assert payload["backend_status"] == {
        "expansion_model": "gemma3:4b",
        "synthesis_model": "ministral-3:8b",
        "gpu_verified": True,
        "last_check": "2026-07-10T00:00:00+00:00",
    }


def test_health_payload_backend_status_none_before_startup_check_ran():
    from aria_rag.api import _health_payload

    class FakeState:
        pass  # no backend_status attribute set yet

    payload = _health_payload(FakeState())
    assert payload["status"] == "ok"
    assert payload["backend_status"] is None
