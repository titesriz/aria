"""Settings/load_settings resolution — see the 2026-09-22 backend-removal
cleanup (sovereignty rule: production only ever uses local Ollama)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag import config as config_module
from aria_rag.config import Settings, load_settings


def test_settings_dataclass_default_backend_is_ollama():
    assert Settings().llm_backend == "ollama"


def test_load_settings_defaults_to_ollama_when_env_unset(monkeypatch):
    # Neutralize .env entirely -- this repo's own .env already sets
    # ARIA_LLM_BACKEND=ollama, which would let a real regression (e.g. the
    # default silently reverting to "openai") pass unnoticed if load_dotenv
    # were allowed to populate the environment here.
    monkeypatch.setattr(config_module, "load_dotenv", lambda: None)
    monkeypatch.delenv("ARIA_LLM_BACKEND", raising=False)

    settings = load_settings()

    assert settings.llm_backend == "ollama"
