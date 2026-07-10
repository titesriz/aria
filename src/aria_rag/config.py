from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DOCS_DIR = ROOT_DIR / "Ressources"
DEFAULT_INDEX_DIR = ROOT_DIR / "data" / "index"

# Per-family slot allocation for scoped (unfiltered) retrieval — sums to the
# default top_k so an unfiltered query's final result set is exactly this
# composition. reglement_graphique and "other" are deliberately excluded:
# both are low-value for narrative Q&A (graphic/plan legends, miscellaneous
# root-level PDFs) and would just consume slots better spent on regulatory
# or contextual content.
# R3c tuning: reglement_ecrit=6 (was 4, rapport_presentation 2->0) + boost=2.0
# (was 1.5) — the minimal combination from the tested matrix that lands the
# Table-1er case at internal rank 4 with a 2-slot margin (rank 8 at boost 1.5
# never fit even at reglement_ecrit=6); verified against golden eval (83/100,
# unaffected — family-filtered) and the overcorrection guard (line-anchored
# arrondissement matching still excludes "1er janvier"/"1er alinéa").
DEFAULT_FAMILY_SLOTS: dict[str, int] = {
    "reglement_ecrit": 6,
    "rapport_presentation": 0,
    "annexes": 2,
    "oap": 1,
    "padd": 1,
}


@dataclass(slots=True)
class Settings:
    docs_dir: Path = DEFAULT_DOCS_DIR
    index_dir: Path = DEFAULT_INDEX_DIR
    chunk_size: int = 1200
    chunk_overlap: int = 200
    top_k: int = 10
    min_alpha_ratio: float = 0.55
    lexical_boost_factor: float = 2.0
    scoped_retrieval: bool = True
    family_slots: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_FAMILY_SLOTS))
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    max_files: int | None = None
    llm_backend: str = "openai"
    openai_api_key: str | None = None
    chat_model: str = "gpt-4.1-mini"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-4-6"
    num_predict: int = 768


def load_settings() -> Settings:
    load_dotenv()
    max_files = os.getenv("ARIA_MAX_FILES")
    return Settings(
        max_files=int(max_files) if max_files else None,
        min_alpha_ratio=float(os.getenv("ARIA_MIN_ALPHA_RATIO", "0.55")),
        lexical_boost_factor=float(os.getenv("ARIA_LEXICAL_BOOST", "2.0")),
        scoped_retrieval=os.getenv("ARIA_SCOPED_RETRIEVAL", "1") not in ("0", "false", "False"),
        embedding_model=os.getenv("ARIA_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"),
        llm_backend=os.getenv("ARIA_LLM_BACKEND", "openai"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        chat_model=os.getenv("ARIA_CHAT_MODEL", "gpt-4.1-mini"),
        ollama_host=os.getenv("ARIA_OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("ARIA_OLLAMA_MODEL", "gemma3:4b"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        claude_model=os.getenv("ARIA_CLAUDE_MODEL", "claude-opus-4-6"),
        num_predict=int(os.getenv("ARIA_NUM_PREDICT", "768")),
    )
