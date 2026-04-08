from __future__ import annotations

from dataclasses import dataclass

import httpx
from openai import OpenAI

from aria_rag.config import Settings
from aria_rag.retriever import SearchHit


SYSTEM_PROMPT = (
    "Answer using only the provided context. If the answer is not in the context, "
    "say so clearly and cite the most relevant sources."
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


def answer_question(question: str, hits: list[SearchHit], settings: Settings, backend: str) -> str:
    normalized = backend.lower()
    if normalized == "openai":
        return answer_with_openai(question, hits, settings)
    if normalized == "ollama":
        return answer_with_ollama(question, hits, settings)
    raise RuntimeError(f"Unsupported LLM backend: {backend}")
