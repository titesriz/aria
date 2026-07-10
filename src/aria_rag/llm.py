from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anthropic
import httpx
from openai import OpenAI

from aria_rag.config import Settings
from aria_rag.retriever import SearchHit


SYSTEM_PROMPT = (
    "Tu es un assistant spécialisé en urbanisme et droit de l'urbanisme français. "
    "Réponds uniquement en français, en te basant exclusivement sur le contexte fourni. "
    "Si la réponse ne figure pas dans le contexte, dis-le clairement et cite les sources les plus pertinentes. "
    "Lorsque tu mentionnes une source, utilise uniquement le nom du fichier (ex: REG1.pdf) "
    "ou une désignation générique (ex: 'le règlement écrit'). "
    "N'inclus jamais de chemin complet ou de chemin absolu dans ta réponse."
)


@dataclass(slots=True)
class PromptBundle:
    system: str
    user: str


def build_prompt(question: str, hits: list[SearchHit]) -> PromptBundle:
    context = "\n\n".join(
        f"Source: {Path(hit.source_path).name}\nContent: {hit.content[:3000]}" for hit in hits
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
        "options": {"temperature": 0, "num_predict": settings.num_predict, "num_ctx": settings.num_ctx},
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
