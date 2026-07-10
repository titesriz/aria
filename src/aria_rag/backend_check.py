"""Ollama backend health check — detects silent CPU fallback.

Twice in this project's history, the Ollama server has silently degraded
to CPU inference (a stale config surviving a botched Vulkan-env-var
restart) while `ollama ps` kept reporting the model as loaded and
answering requests — just far slower, no error raised, quietly
confounding latency measurements each time. This module makes that
failure mode loud instead of silent: force-load a model, then query
Ollama's /api/ps to confirm it landed fully in VRAM (size_vram == size).
A CPU-resident model still answers; only /api/ps tells the two apart.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from aria_rag.config import Settings

logger = logging.getLogger(__name__)

_LOAD_TIMEOUT = 60.0
_PS_TIMEOUT = 10.0


@dataclass(slots=True)
class ModelGpuCheck:
    model: str
    gpu_verified: bool
    size: int | None = None
    size_vram: int | None = None
    error: str | None = None


@dataclass(slots=True)
class BackendStatus:
    expansion_model: str
    synthesis_model: str
    gpu_verified: bool
    last_check: str
    checked: bool = True
    expansion_check: ModelGpuCheck | None = None
    synthesis_check: ModelGpuCheck | None = None


def check_model_gpu(ollama_host: str, model: str) -> ModelGpuCheck:
    """Force-load `model` with a trivial generate call, then confirm via
    /api/ps that it is 100% VRAM-resident (size_vram >= size). Never
    raises — a failed check is reported via ModelGpuCheck.error, not an
    exception, since callers (startup checks, /health) must never crash
    the process over a diagnostic.
    """
    generate_url = f"{ollama_host.rstrip('/')}/api/generate"
    ps_url = f"{ollama_host.rstrip('/')}/api/ps"
    try:
        response = httpx.post(
            generate_url,
            json={"model": model, "prompt": "ok", "stream": False, "options": {"num_predict": 1}},
            timeout=_LOAD_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return ModelGpuCheck(model=model, gpu_verified=False, error=f"load failed: {exc}")

    try:
        response = httpx.get(ps_url, timeout=_PS_TIMEOUT)
        response.raise_for_status()
        loaded = response.json().get("models", [])
    except httpx.HTTPError as exc:
        return ModelGpuCheck(model=model, gpu_verified=False, error=f"/api/ps failed: {exc}")

    for entry in loaded:
        if entry.get("model") == model or entry.get("name") == model:
            size = entry.get("size")
            size_vram = entry.get("size_vram")
            gpu_ok = size is not None and size_vram is not None and size_vram >= size
            return ModelGpuCheck(model=model, gpu_verified=gpu_ok, size=size, size_vram=size_vram)

    return ModelGpuCheck(model=model, gpu_verified=False, error="model not found in /api/ps after load")


def verify_backend(settings: Settings) -> BackendStatus:
    """Check both configured models land 100% GPU. They don't co-reside
    (known ~8GB VRAM constraint), so each is loaded and checked in turn,
    evicting the other — this mirrors real request-time behavior rather
    than testing an unrealistic co-resident state.
    """
    now = datetime.now(timezone.utc).isoformat()
    if settings.llm_backend != "ollama":
        return BackendStatus(
            expansion_model=settings.expansion_model,
            synthesis_model=settings.synthesis_model,
            gpu_verified=True,
            last_check=now,
            checked=False,
        )

    expansion_check = check_model_gpu(settings.ollama_host, settings.expansion_model)
    synthesis_check = check_model_gpu(settings.ollama_host, settings.synthesis_model)
    gpu_verified = expansion_check.gpu_verified and synthesis_check.gpu_verified

    for check, stage in ((expansion_check, "expansion"), (synthesis_check, "synthesis")):
        if check.gpu_verified:
            logger.info(
                "Backend check: %s model %s — 100%% GPU (size=%s size_vram=%s)",
                stage, check.model, check.size, check.size_vram,
            )
        else:
            logger.error(
                "Backend check: %s model %s — NOT verified 100%% GPU (%s)",
                stage, check.model, check.error or f"size={check.size} size_vram={check.size_vram}",
            )

    return BackendStatus(
        expansion_model=settings.expansion_model,
        synthesis_model=settings.synthesis_model,
        gpu_verified=gpu_verified,
        last_check=now,
        expansion_check=expansion_check,
        synthesis_check=synthesis_check,
    )


def startup_should_refuse(llm_backend: str, backend_status: BackendStatus, allow_cpu: bool) -> bool:
    """Pure decision: should `serve` refuse to start over this backend_status?"""
    return llm_backend == "ollama" and not backend_status.gpu_verified and not allow_cpu


def is_model_resident(ollama_host: str, model: str) -> bool | None:
    """True if `model` is currently loaded (the next request won't need a
    load/swap), False if not, None if the check itself failed (e.g.
    Ollama unreachable) — callers must treat None as "unknown", not as
    "not resident".
    """
    try:
        response = httpx.get(f"{ollama_host.rstrip('/')}/api/ps", timeout=_PS_TIMEOUT)
        response.raise_for_status()
        loaded = response.json().get("models", [])
    except httpx.HTTPError:
        return None
    return any(entry.get("model") == model or entry.get("name") == model for entry in loaded)
