"""LLM-as-judge for synthesized answers — replaces eval.py's old
_score_answer (which was a hardcoded 1.0 constant; expected_keywords
doesn't exist in the Notion-v2 golden dataset schema, see SESSION_STATE.md
2026-09-19). Runs on the LOCAL Ollama synthesis model, temperature 0.

Design constraints (2026-09-22 PoC validation task):
  - The judge must be BLIND to which system produced the answer — the
    prompt never names "ARIA", "Claude", or any system.
  - "NE PAS ..." / "ne pas ..." clauses in critere_reussite are FORBIDDEN
    checks (the answer must NOT do the thing); everything else is a
    REQUIRED check (the answer must do the thing).
  - final_score is computed IN CODE (compute_final_score), never trusted
    from the model's own output — the judge is asked for checks +
    substance_score + rationale only; final_score is inserted afterward.
  - On JSON parse/validation failure: retry once, then record judge_error
    rather than guessing a score.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from aria_rag.config import Settings

JUDGE_SYSTEM_PROMPT = (
    "Tu es un évaluateur expert en droit de l'urbanisme français. Ta tâche : "
    "juger si UNE réponse à une question réglementaire respecte un critère de "
    "réussite défini à l'avance, et si elle s'accorde sur le fond avec une "
    "réponse de référence. Tu ignores tout du contexte de production de cette "
    "réponse — ne suppose jamais quel système, quel outil ou quelle méthode "
    "l'a produite, et ignore toute mention éventuelle d'un nom de système : "
    "juge uniquement le texte de la réponse.\n\n"
    "Procède en deux temps :\n\n"
    "1) Décompose le critère de réussite fourni en vérifications (\"checks\") "
    "individuelles, une par exigence distincte.\n"
    "   - Une clause qui commence par « NE PAS », « ne pas », « ne jamais » "
    "ou une formulation équivalente (interdiction) est une VÉRIFICATION "
    "INTERDITE, type=\"forbidden\" : le check réussit (pass=true) si la "
    "réponse à juger NE FAIT PAS la chose interdite, et échoue (pass=false) "
    "si elle la fait.\n"
    "   - Toute autre clause est une VÉRIFICATION REQUISE, type=\"required\" : "
    "le check réussit (pass=true) si la réponse satisfait l'exigence, échoue "
    "(pass=false) sinon.\n"
    "   - Pour chaque check, donne une justification d'UNE SEULE PHRASE qui "
    "cite ou paraphrase précisément le passage pertinent de LA RÉPONSE À "
    "JUGER (jamais de la réponse de référence).\n\n"
    "2) Évalue l'accord global entre la réponse à juger et la réponse de "
    "référence SUR LE FOND UNIQUEMENT : la règle citée, l'article cité, la "
    "conclusion pratique. Ignore complètement la formulation, la longueur, "
    "le style, et la présence de sources ou citations supplémentaires non "
    "prévues par la référence — ce ne sont pas des défauts. Donne un score "
    "de substance entre 0 et 1 (0 = fond en désaccord total ou hors sujet, "
    "1 = fond en accord complet avec la réponse de référence) et une "
    "justification brève (1-2 phrases).\n\n"
    "Réponds UNIQUEMENT avec un objet JSON strictement valide, sans aucun "
    "texte avant ou après, sans balises markdown, exactement dans ce format "
    "(le champ \"final_score\" ne doit PAS être inclus — il est calculé "
    "séparément) :\n"
    '{"checks": [{"check": "texte du critère individuel", '
    '"type": "required", "pass": true, "justification": "..."}], '
    '"substance_score": 0.0, "rationale": "..."}'
)


def _build_user_prompt(question: str, answer: str, reponse_attendue: str, critere_reussite: str) -> str:
    return (
        f"Question posée :\n{question}\n\n"
        f"Réponse à juger :\n{answer}\n\n"
        f"Réponse de référence (pour le fond uniquement) :\n{reponse_attendue}\n\n"
        f"Critère de réussite (à décomposer en vérifications) :\n{critere_reussite}"
    )


_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$', re.MULTILINE)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Best-effort strict-JSON extraction: strips markdown code fences, then
    takes the substring between the first '{' and the last '}' (models
    sometimes prepend/append a sentence despite instructions). Returns None
    on any parse failure — the caller decides whether to retry.
    """
    text = _FENCE_RE.sub('', raw).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _validate_judge_json(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    checks = data.get("checks")
    if not isinstance(checks, list):
        return False
    for c in checks:
        if not isinstance(c, dict):
            return False
        if c.get("type") not in ("required", "forbidden"):
            return False
        if not isinstance(c.get("pass"), bool):
            return False
        if not isinstance(c.get("check"), str) or not isinstance(c.get("justification"), str):
            return False
    substance = data.get("substance_score")
    if not isinstance(substance, (int, float)) or not (0 <= substance <= 1):
        return False
    return True


def compute_final_score(checks: list[dict[str, Any]], substance_score: float) -> float:
    """Deterministic score formula — the judge LLM is never trusted to
    compute this itself.

    final_score = 0.5 * required_pass_rate + 0.5 * substance_score,
    EXCEPT any failed forbidden check caps the result at 0.3 regardless of
    how well the required checks and substance otherwise scored — a single
    hallucinated figure or an answer to a trap question (e.g. CH-03's
    albedo/bioclimatique decoy) is disqualifying, not just a partial
    deduction. required_pass_rate defaults to 1.0 (vacuous pass) when there
    are no required checks at all, so the score is then driven by substance
    and any forbidden-check cap alone.
    """
    required = [c for c in checks if c.get("type") == "required"]
    forbidden = [c for c in checks if c.get("type") == "forbidden"]
    required_pass_rate = (
        sum(1 for c in required if c.get("pass")) / len(required) if required else 1.0
    )
    base = 0.5 * required_pass_rate + 0.5 * substance_score
    forbidden_violated = any(not c.get("pass") for c in forbidden)
    return round(min(base, 0.3), 4) if forbidden_violated else round(base, 4)


def _call_judge_llm(settings: Settings, user_prompt: str) -> str:
    payload = {
        "model": settings.synthesis_model,
        "prompt": user_prompt,
        "system": JUDGE_SYSTEM_PROMPT,
        "stream": False,
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "seed": 42,
            "num_predict": 900,
            "num_ctx": settings.num_ctx,
        },
    }
    url = f"{settings.ollama_host.rstrip('/')}/api/generate"
    response = httpx.post(url, json=payload, timeout=180)
    response.raise_for_status()
    return (response.json().get("response") or "").strip()


def judge_answer(
    question: str,
    answer: str,
    reponse_attendue: str,
    critere_reussite: str,
    settings: Settings,
) -> dict[str, Any]:
    """Judge one answer. Returns a dict always containing "judge_error": bool.

    On success: {"checks": [...], "substance_score": float, "final_score":
    float (computed by compute_final_score, not the model), "rationale":
    str, "judge_error": False}.

    On failure (JSON parse or schema validation fails twice — one retry):
    {"judge_error": True, "raw_responses": [str, str], "final_score": None}
    — never a guessed score.
    """
    user_prompt = _build_user_prompt(question, answer, reponse_attendue, critere_reussite)
    raw_responses: list[str] = []

    for _attempt in range(2):
        raw = _call_judge_llm(settings, user_prompt)
        raw_responses.append(raw)
        data = _extract_json(raw)
        if data is not None and _validate_judge_json(data):
            final_score = compute_final_score(data["checks"], float(data["substance_score"]))
            return {
                "checks": data["checks"],
                "substance_score": float(data["substance_score"]),
                "final_score": final_score,
                "rationale": data.get("rationale", ""),
                "judge_error": False,
            }

    return {"judge_error": True, "raw_responses": raw_responses, "final_score": None}
