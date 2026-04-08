from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DOCS_DIR = ROOT_DIR / "Ressources"
DEFAULT_INDEX_DIR = ROOT_DIR / "data" / "index"


@dataclass(slots=True)
class Settings:
    docs_dir: Path = DEFAULT_DOCS_DIR
    index_dir: Path = DEFAULT_INDEX_DIR
    chunk_size: int = 1200
    chunk_overlap: int = 200
    top_k: int = 5
    max_files: int | None = None
    llm_backend: str = "openai"
    openai_api_key: str | None = None
    chat_model: str = "gpt-4.1-mini"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"


def load_settings() -> Settings:
    load_dotenv()
    max_files = os.getenv("ARIA_MAX_FILES")
    return Settings(
        max_files=int(max_files) if max_files else None,
        llm_backend=os.getenv("ARIA_LLM_BACKEND", "openai"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        chat_model=os.getenv("ARIA_CHAT_MODEL", "gpt-4.1-mini"),
        ollama_host=os.getenv("ARIA_OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("ARIA_OLLAMA_MODEL", "gemma3:4b"),
    )
