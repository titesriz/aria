from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import anthropic
import httpx
from openai import OpenAI

from aria_rag.config import Settings
from aria_rag.retriever import SearchHit


SYSTEM_PROMPT = (
    "Tu es un assistant spécialisé en urbanisme et droit de l'urbanisme français. "
    "Réponds uniquement en français, en te basant exclusivement sur le contexte fourni : "
    "n'avance aucun fait, chiffre, ou règle qui n'y figure pas explicitement, même s'il te "
    "semble plausible ou générique pour ce domaine, et n'introduis jamais de notion "
    "réglementaire absente du contexte (ex: POS, COS, ZPPAUP). "
    "\n\n"
    "Avant de répondre, distingue explicitement trois cas, sans jamais laisser le "
    "premier ou le deuxième glisser vers le troisième : "
    "(a) le contexte contient la règle qui répond à la question — tu la donnes, "
    "en citant les passages qui la portent ; "
    "(b) le contexte contient des éléments partiels ou connexes (une disposition "
    "qui touche au sujet sans trancher la question précise) — tu donnes ces "
    "éléments, cités, ET tu signales précisément ce qui manque pour trancher ; "
    "(c) le contexte ne contient rien de pertinent sur le sujet — tu le dis "
    "explicitement (« le contexte fourni ne précise pas... »). "
    "Une règle absolue découle de cette distinction : si tu cites une disposition "
    "du contexte à l'appui d'un point, tu ne peux pas déclarer dans la même "
    "réponse, pour ce même point, une absence d'information — cite ce que tu as "
    "trouvé (cas a ou b), ou déclare l'absence (cas c), jamais les deux à la fois. "
    "Le constat de lacune ne porte que sur ce qui manque réellement, jamais sur "
    "un aspect que le contexte couvre déjà. "
    "\n\n"
    "Quand la question porte sur une règle applicable à une zone donnée par "
    "l'utilisateur (n'infère jamais une zone à partir d'une adresse), structure "
    "ta réponse en deux temps si le contexte le permet : d'abord la règle "
    "générale de cette zone, puis les exceptions, dérogations ou conditions "
    "particulières à vérifier (secteurs spécifiques, seuils, cas dérogatoires) "
    "que le contexte mentionne. "
    "\n\n"
    "Sois documenté : cite et développe le contenu des passages pertinents "
    "plutôt que de résumer en une phrase courte — « documenté » signifie citer "
    "davantage le contexte fourni, jamais ajouter des connaissances extérieures. "
    "Si un aspect de la question n'est pas couvert par le contexte, dis-le "
    "explicitement (« le contexte fourni ne précise pas... ») et arrête-toi là "
    "pour cet aspect précis — ne le complète pas avec des connaissances "
    "générales, des valeurs typiques, ou des clauses en « généralement »/"
    "« habituellement » portant un chiffre ou une notion juridique absente du "
    "contexte. "
    "\n\n"
    "Cite les sources les plus pertinentes du contexte. Lorsque tu mentionnes "
    "une source, utilise uniquement le nom du fichier (ex: REG1.pdf) ou une "
    "désignation générique (ex: 'le règlement écrit'). N'inclus jamais de "
    "chemin complet ou de chemin absolu dans ta réponse. "
    "Le contexte est découpé en passages numérotés [1], [2], etc. Chaque "
    "affirmation factuelle doit porter le marqueur [N] du passage qui la fonde "
    "(ex : « ...doivent être implantées à l'alignement [3]. »). Toute "
    "affirmation qui ne peut porter aucun marqueur doit être supprimée ou "
    "explicitement déplacée dans le constat de lacune."
)

_MARKER_RE = re.compile(r'\[(\d+)\]')


def extract_cited_markers(answer: str, num_chunks: int) -> set[int] | None:
    """Parse [N] markers out of a synthesized answer.

    Returns the set of valid, in-range marker numbers (1..num_chunks) actually
    cited in `answer`, or None if the answer carries no markers at all — the
    caller's signal to fall back to `used=None` for every citation rather than
    treating an unmarked answer as "cited nothing." Markers are progressive
    enhancement (older prompts/backends may not produce them), never a
    correctness requirement, so this never raises: a regex match failure is
    not possible here, but any future parsing complexity added to this
    function must preserve that guarantee.
    """
    found = {int(m) for m in _MARKER_RE.findall(answer)}
    if not found:
        return None
    in_range = {n for n in found if 1 <= n <= num_chunks}
    # All markers present were out of range (e.g. [99] with 8 chunks) — as
    # unusable as no markers at all, so fall back the same way rather than
    # reporting every citation as unused.
    return in_range or None


@dataclass(slots=True)
class PromptBundle:
    system: str
    user: str


def build_prompt(question: str, hits: list[SearchHit]) -> PromptBundle:
    context = "\n\n".join(
        f"[{i}] Source: {Path(hit.source_path).name}\nContent: {hit.content[:3000]}"
        for i, hit in enumerate(hits, start=1)
    )
    return PromptBundle(
        system=SYSTEM_PROMPT,
        user=f"Question: {question}\n\nContext:\n{context}",
    )


def answer_with_openai(question: str, hits: list[SearchHit], settings: Settings) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    prompt = build_prompt(question, hits)
    client = OpenAI(api_key=settings.openai_api_key, timeout=120)
    try:
        response = client.responses.create(
            model=settings.chat_model,
            input=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            max_output_tokens=settings.num_predict,
        )
    except openai.OpenAIError as exc:
        raise RuntimeError(f"OpenAI request failed ({type(exc).__name__}). Please try again.") from exc

    text = (response.output_text or "").strip()
    if not text:
        raise RuntimeError("OpenAI returned an empty response.")
    return text


def answer_with_ollama(question: str, hits: list[SearchHit], settings: Settings) -> str:
    prompt = build_prompt(question, hits)
    payload = {
        "model": settings.synthesis_model,
        "prompt": prompt.user,
        "system": prompt.system,
        "stream": False,
        "keep_alive": "10m",
        # seed pins full reproducibility on top of temperature=0 -- see
        # query_expansion.py's _expand_with_ollama for the R0 precedent this
        # mirrors (Ollama's /api/generate only honors params nested under
        # "options"; a top-level "temperature" key is silently ignored).
        "options": {
            "temperature": 0,
            "seed": 42,
            "num_predict": settings.num_predict,
            "num_ctx": settings.num_ctx,
        },
    }
    url = f"{settings.ollama_host.rstrip('/')}/api/generate"

    try:
        response = httpx.post(url, json=payload, timeout=300)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Ollama request failed. Make sure Ollama is installed, the app or service is "
            f"running, and model `{settings.synthesis_model}` is available at {settings.ollama_host}."
        ) from exc

    data = response.json()
    text = (data.get("response") or "").strip()
    if not text:
        raise RuntimeError("Ollama returned an empty response.")
    return text


def answer_with_claude(question: str, hits: list[SearchHit], settings: Settings) -> str:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    prompt = build_prompt(question, hits)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=120)

    # Stream the response; use prompt caching on the stable system prompt.
    full_text: list[str] = []
    try:
        with client.messages.stream(
            model=settings.claude_model,
            max_tokens=settings.num_predict,
            system=[
                {
                    "type": "text",
                    "text": prompt.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt.user}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                full_text.append(text)
        print()  # newline after streaming
    except anthropic.AnthropicError as exc:
        raise RuntimeError(f"Claude request failed ({type(exc).__name__}). Please try again.") from exc

    text = "".join(full_text).strip()
    if not text:
        raise RuntimeError("Claude returned an empty response.")
    return text


def answer_question(question: str, hits: list[SearchHit], settings: Settings, backend: str) -> str:
    normalized = backend.lower()
    if normalized == "openai":
        return answer_with_openai(question, hits, settings)
    if normalized == "ollama":
        return answer_with_ollama(question, hits, settings)
    if normalized == "claude":
        return answer_with_claude(question, hits, settings)
    raise RuntimeError(f"Unsupported LLM backend: {backend}")
