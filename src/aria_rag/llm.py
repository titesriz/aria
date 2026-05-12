from __future__ import annotations

from dataclasses import dataclass

import anthropic
import httpx
from openai import OpenAI

from aria_rag.config import Settings
from aria_rag.retriever import SearchHit


SYSTEM_PROMPT = (
    "Tu es un assistant spécialisé en urbanisme et droit de l'urbanisme français. "
    "Réponds uniquement en français, en te basant exclusivement sur le contexte fourni. "
    "Si la réponse ne figure pas dans le contexte, dis-le clairement et cite les sources les plus pertinentes."
)


@dataclass(slots=True)
class PromptBundle:
    system: str
    user: str


def build_prompt(question: str, hits: list[SearchHit]) -> PromptBundle:
    context = "\n\n".join(
        f"Source: {hit.source_path}\nContent: {hit.content[:3000]}" for hit in hits
    )
    return PromptBundle(
        system=SYSTEM_PROMPT,
        user=f"Question: {question}\n\nContext:\n{context}",
    )


def answer_with_openai(question: str, hits: list[SearchHit], settings: Settings) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    prompt = build_prompt(question, hits)
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.chat_model,
        input=[
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ],
    )
    return response.output_text.strip()


def answer_with_ollama(question: str, hits: list[SearchHit], settings: Settings) -> str:
    prompt = build_prompt(question, hits)
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt.user,
        "system": prompt.system,
        "stream": False,
        "keep_alive": "10m",
    }
    url = f"{settings.ollama_host.rstrip('/')}/api/generate"

    try:
        response = httpx.post(url, json=payload, timeout=300)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Ollama request failed. Make sure Ollama is installed, the app or service is "
            f"running, and model `{settings.ollama_model}` is available at {settings.ollama_host}."
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
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Stream the response; use prompt caching on the stable system prompt.
    full_text: list[str] = []
    with client.messages.stream(
        model=settings.claude_model,
        max_tokens=4096,
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
    return "".join(full_text)


def answer_question(question: str, hits: list[SearchHit], settings: Settings, backend: str) -> str:
    normalized = backend.lower()
    if normalized == "openai":
        return answer_with_openai(question, hits, settings)
    if normalized == "ollama":
        return answer_with_ollama(question, hits, settings)
    if normalized == "claude":
        return answer_with_claude(question, hits, settings)
    raise RuntimeError(f"Unsupported LLM backend: {backend}")
