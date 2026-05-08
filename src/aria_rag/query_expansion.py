"""Query expansion — infers likely PLU article codes from a natural-language question."""
from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)

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
    "Q: \"Peut-on implanter en retrait par rapport à la voie ?\"\n"
    "R: [\"UG.3.1\", \"UG.3.1.1\"]\n\n"
    "Q: \"Quelle proportion de logements sociaux pour 30 logements neufs ?\"\n"
    "R: [\"UG.1.5\", \"UG.1.5.1\"]"
)

_TIMEOUT = 15


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


def _expand_with_ollama(question: str, ollama_host: str, ollama_model: str) -> list[str]:
    payload = {
        "model": ollama_model,
        "prompt": question,
        "system": _SYSTEM_PROMPT,
        "stream": False,
    }
    url = f"{ollama_host.rstrip('/')}/api/generate"
    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT)
        response.raise_for_status()
        raw = response.json().get("response", "")
        return _parse_articles(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Query expansion failed (ollama): %s", exc)
        return []


def expand_query(
    question: str,
    backend: str = "ollama",
    ollama_host: str = "http://localhost:11434",
    ollama_model: str = "gemma3:4b",
) -> tuple[str, str, list[str]]:
    """Return (question_originale, expansion_query, inferred_articles).

    expansion_query = article codes joined by spaces (e.g. "UG.3.1 UG.3.1.1").
    Falls back to (question, "", []) on any error or when no articles are inferred.
    """
    if backend == "ollama":
        articles = _expand_with_ollama(question, ollama_host, ollama_model)
    else:
        logger.debug("Query expansion not implemented for backend=%s, skipping.", backend)
        return question, "", []

    if not articles:
        logger.debug("Query expansion returned no articles.")
        return question, "", []

    logger.debug("Query expansion inferred articles: %s", articles)
    expansion_query = " ".join(articles)
    return question, expansion_query, articles
