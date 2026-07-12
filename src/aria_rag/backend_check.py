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
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aria_rag.config import Settings

logger = logging.getLogger(__name__)

_LOAD_TIMEOUT = 60.0
_PS_TIMEOUT = 10.0

# The cold-boot race: `aria-rag serve` starts before Ollama is listening on
# :11434 yet, so the first model's check gets a connection refused while the
# second (seconds later) succeeds. 5 attempts, doubling from 2s, covers the
# handful of seconds Ollama takes to come up without masking a genuinely
# down Ollama forever.
_GPU_CHECK_ATTEMPTS = 5
_GPU_CHECK_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)


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


def _load_and_verify_gpu_once(ollama_host: str, model: str) -> ModelGpuCheck:
    """One attempt: force-load `model`, confirm via /api/ps it's 100%
    VRAM-resident. Raises httpx.ConnectError if Ollama itself isn't
    reachable — that's the cold-boot race, and callers decide whether it's
    worth retrying. Any other httpx failure (bad status, timeout, model
    missing) is reported via ModelGpuCheck.error, never raised: it's a
    real, distinct failure, not a "server isn't up yet" race.
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
    except httpx.ConnectError:
        raise
    except httpx.HTTPError as exc:
        return ModelGpuCheck(model=model, gpu_verified=False, error=f"load failed: {exc}")

    try:
        response = httpx.get(ps_url, timeout=_PS_TIMEOUT)
        response.raise_for_status()
        loaded = response.json().get("models", [])
    except httpx.ConnectError:
        raise
    except httpx.HTTPError as exc:
        return ModelGpuCheck(model=model, gpu_verified=False, error=f"/api/ps failed: {exc}")

    for entry in loaded:
        if entry.get("model") == model or entry.get("name") == model:
            size = entry.get("size")
            size_vram = entry.get("size_vram")
            gpu_ok = size is not None and size_vram is not None and size_vram >= size
            return ModelGpuCheck(model=model, gpu_verified=gpu_ok, size=size, size_vram=size_vram)

    return ModelGpuCheck(model=model, gpu_verified=False, error="model not found in /api/ps after load")


def check_model_gpu(ollama_host: str, model: str) -> ModelGpuCheck:
    """Single-attempt check. Never raises — a failed check (including
    Ollama being unreachable) is reported via ModelGpuCheck.error, since
    callers must never crash the process over a diagnostic. Use
    check_model_gpu_with_retry at startup, where the cold-boot race
    actually happens; this single-shot version is for callers (e.g. a
    request-time re-check) where Ollama being down is not a race to wait
    out but a fact to report immediately.
    """
    try:
        return _load_and_verify_gpu_once(ollama_host, model)
    except httpx.ConnectError as exc:
        return ModelGpuCheck(model=model, gpu_verified=False, error=f"load failed: {exc}")


def check_model_gpu_with_retry(
    ollama_host: str,
    model: str,
    attempts: int = _GPU_CHECK_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = _GPU_CHECK_BACKOFF_SECONDS,
) -> ModelGpuCheck:
    """Retries only the cold-boot race (Ollama not listening yet right
    after `aria-rag serve` starts). A model that IS reachable but lands on
    CPU is a real, distinct failure reported immediately on the first
    attempt — never retried, so a genuine CPU fallback doesn't turn into a
    30s wait before failing exactly as loudly as before.
    """
    last_exc: httpx.ConnectError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _load_and_verify_gpu_once(ollama_host, model)
        except httpx.ConnectError as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
            logger.warning(
                "Backend check: %s (%s) not reachable yet, attempt %d/%d — retrying in %ss: %s",
                model, ollama_host, attempt, attempts, delay, exc,
            )
            print(
                f"[backend check] {model}: Ollama not reachable yet (attempt {attempt}/{attempts}), "
                f"retrying in {delay}s...",
                flush=True,
            )
            time.sleep(delay)

    logger.error("Backend check: %s — Ollama not reachable after %d attempts: %s", model, attempts, last_exc)
    return ModelGpuCheck(
        model=model, gpu_verified=False,
        error=f"load failed after {attempts} attempts (Ollama unreachable): {last_exc}",
    )


def resolve_git_commit(repo_root: Path) -> str:
    """Short git commit hash for the startup banner and /health's
    git_commit — the ghost-server killer: a stale `aria-rag serve`
    process serving old code is otherwise indistinguishable from a fresh
    one. Never raises; "unknown" covers no git installed, not a repo, or
    any other failure, since a version-visibility diagnostic must never
    crash startup.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


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

    expansion_check = check_model_gpu_with_retry(settings.ollama_host, settings.expansion_model)
    synthesis_check = check_model_gpu_with_retry(settings.ollama_host, settings.synthesis_model)
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
