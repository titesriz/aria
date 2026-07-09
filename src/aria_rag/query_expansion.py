"""Query expansion — infers likely PLU article codes from a natural-language question."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Shared cache — eval.py defines its own DEFAULT_EXPANSION_CACHE pointing at
# the same path; this is the canonical constant for other callers (aria_rag.api)
# so an interactively-typed question and a golden-dataset question can reuse
# each other's cached expansion.
DEFAULT_EXPANSION_CACHE_PATH = _REPO_ROOT / "eval" / "expansion_cache.json"

_SYSTEM_PROMPT = (
    "Tu es un expert du PLU bioclimatique de Paris. "
    "À partir d'une question en langage naturel sur l'urbanisme parisien, "
    "identifie les codes d'articles PLU les plus probablement pertinents.\n\n"
    "Réponds UNIQUEMENT avec une liste JSON de codes articles, sans texte autour.\n"
    "Format : [\"UG.3.1.1\", \"UG.3.2\"]\n"
    "Maximum 4 codes. Si incertain, retourne [].\n\n"
    "Exemples :\n"
    "Q: \"Quelle hauteur maximale pour une construction neuve zone UG ?\"\n"
    "R: [\"UG.3.2\", \"UG.3.2.1\"]\n\n"
    "Q: \"Quel est le plafond de hauteur indiqué au plan général des hauteurs ?\"\n"
    "R: [\"UG.3.2.1\", \"UG.3.2\"]\n\n"
    "Q: \"Peut-on implanter en retrait par rapport à la voie ?\"\n"
    "R: [\"UG.3.1\", \"UG.3.1.1\"]\n\n"
    "Q: \"Quelle proportion de logements sociaux pour 30 logements neufs ?\"\n"
    "R: [\"UG.1.5\", \"UG.1.5.1\"]\n\n"
    "Q: \"Un local de bureaux peut-il être transformé en hôtel ?\"\n"
    "R: [\"UG.1.3\", \"UG.1.3.3\"]\n\n"
    "Q: \"Quelle mixité fonctionnelle entre bureaux et logements est imposée ?\"\n"
    "R: [\"UG.1.4.1\", \"UG.1.4\"]\n\n"
    "Q: \"Puis-je changer la couverture de ma toiture (tuile, zinc) ou l'aspect de ma façade ?\"\n"
    "R: [\"UG.2.2.3\", \"UG.2.2\"]\n\n"
    "Q: \"Quelles occupations et utilisations du sol sont interdites, sans zone précisée ?\"\n"
    "R: [\"UG.1.1\", \"N.1.1\", \"UV.1.1\", \"UGSU.1.1\"]"
)

_TIMEOUT = 60  # CPU-backed Ollama inference can exceed 15s; a timeout here silently
# falls back to no expansion, which is itself a source of run-to-run variance.

_ARTICLE_WHITELIST_PATH = Path(__file__).resolve().parents[2] / "eval" / "article_whitelist.json"
_whitelist_cache: set[str] | None = None
_whitelist_loaded = False


def _load_whitelist() -> set[str] | None:
    """Corpus-derived set of article codes that actually exist in the index
    (written by indexer.build_index). Used to reject LLM hallucinations like
    "DG.2.7" — a prefix that doesn't even exist in this corpus's naming
    convention. Returns None (skip filtering) if the file doesn't exist yet
    (e.g. before the first ingest with this feature).
    """
    global _whitelist_cache, _whitelist_loaded
    if not _whitelist_loaded:
        if _ARTICLE_WHITELIST_PATH.exists():
            _whitelist_cache = set(json.loads(_ARTICLE_WHITELIST_PATH.read_text(encoding="utf-8")))
        else:
            logger.debug("No article whitelist found at %s — skipping validation.", _ARTICLE_WHITELIST_PATH)
            _whitelist_cache = None
        _whitelist_loaded = True
    return _whitelist_cache


def _parse_articles(raw: str) -> list[str]:
    """Extract a list of article codes from the LLM response. Returns [] on any parse failure."""
    text = raw.strip()
    # Find the first [...] block in the response
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if isinstance(item, str) and item.strip()]
    except json.JSONDecodeError:
        return []


def _expand_with_ollama(question: str, ollama_host: str, ollama_model: str) -> tuple[list[str], str]:
    """Return (articles, status). status is "ok" (articles inferred), "empty"
    (no exception, but no usable articles — none returned or all filtered as
    hallucinations), or "failed" (the Ollama call itself raised, including
    the _TIMEOUT above) — logged as an ERROR so a silent fallback to the
    original query is never mistaken for a genuine "no relevant articles"
    answer.
    """
    payload = {
        "model": ollama_model,
        "prompt": question,
        "system": _SYSTEM_PROMPT,
        "stream": False,
        # Ollama's /api/generate only honors model params nested under
        # "options" — a top-level "temperature" key is silently ignored,
        # leaving the model at its default (non-zero) temperature. seed is
        # set for full reproducibility on top of temperature=0.
        "options": {
            "temperature": 0,
            "seed": 42,
        },
    }
    url = f"{ollama_host.rstrip('/')}/api/generate"
    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT)
        response.raise_for_status()
        raw = response.json().get("response", "")
        articles = _parse_articles(raw)
    except Exception as exc:  # noqa: BLE001
        logger.error("Query expansion FAILED (ollama) for question=%r: %s", question, exc)
        return [], "failed"

    whitelist = _load_whitelist()
    if whitelist is None:
        return articles, ("ok" if articles else "empty")
    valid = [a for a in articles if a in whitelist]
    dropped = [a for a in articles if a not in whitelist]
    if dropped:
        logger.debug("Dropping hallucinated article code(s) not in corpus whitelist: %s", dropped)
    return valid, ("ok" if valid else "empty")


def _cache_key(question: str) -> str:
    """Normalize a question into a stable cache key — collapses whitespace
    and casefolds, so trivial interactive variations (extra spaces,
    capitalization) reuse a cached expansion instead of paying a fresh
    7-28s CPU-bound Ollama call every time. Only the cache KEY is
    normalized; the `question` value expand_query returns is always the
    caller's original, unmodified text.
    """
    return re.sub(r'\s+', ' ', question.strip()).casefold()


def _load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    # Re-key by normalized form on every load so cache files written before
    # normalization (raw question text as key) keep matching — the next
    # write then persists the re-keyed (normalized) form, self-migrating
    # the file over time without a one-off migration script.
    return {_cache_key(k): v for k, v in raw.items()}


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def expand_query(
    question: str,
    backend: str = "ollama",
    ollama_host: str = "http://localhost:11434",
    ollama_model: str = "gemma3:4b",
    cache_path: Path | str | None = None,
    refresh: bool = False,
) -> tuple[str, str, list[str], str]:
    """Return (question_originale, expansion_query, inferred_articles, expansion_status).

    expansion_query = article codes joined by spaces (e.g. "UG.3.1 UG.3.1.1").
    expansion_status is "ok" (articles inferred), "empty" (no exception, but
    no usable articles), or "failed" (the LLM call raised/timed out — see
    _expand_with_ollama). Falls back to (question, "", [], status) on any
    error or when no articles are inferred — callers must check
    expansion_status, not just an empty inferred_articles list, to tell a
    genuine "no relevant articles" answer apart from a silent failure.

    cache_path: if given, results (including expansion_status) are cached on
    disk keyed by question text. Even with temperature=0 and a fixed seed,
    CPU-backed Ollama inference can occasionally differ by a token
    (floating-point non-associativity in multi-threaded matmul reduction) —
    callers that need run-to-run reproducibility (e.g. eval) should pass a
    cache_path. refresh=True forces recomputation and overwrites the cached
    entry.
    """
    cache: dict | None = None
    cache_key = _cache_key(question)
    if cache_path is not None:
        cache_path = Path(cache_path)
        cache = _load_cache(cache_path)
        if not refresh and cache_key in cache:
            entry = cache[cache_key]
            # Older cache entries predate expansion_status — infer it from
            # inferred_articles so existing caches don't need invalidating.
            status = entry.get("expansion_status") or ("ok" if entry["inferred_articles"] else "empty")
            return question, entry["expansion_query"], entry["inferred_articles"], status

    if backend == "ollama":
        articles, status = _expand_with_ollama(question, ollama_host, ollama_model)
    else:
        logger.debug("Query expansion not implemented for backend=%s, skipping.", backend)
        return question, "", [], "empty"

    expansion_query = " ".join(articles) if articles else ""

    if cache is not None:
        cache[cache_key] = {
            "expansion_query": expansion_query,
            "inferred_articles": articles,
            "expansion_status": status,
        }
        _save_cache(cache_path, cache)

    if not articles:
        logger.debug("Query expansion returned no articles.")
        return question, "", [], status

    logger.debug("Query expansion inferred articles: %s", articles)
    return question, expansion_query, articles, status
