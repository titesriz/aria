"""Evaluation module — runs aria-rag ask end-to-end and scores against a golden dataset."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parent.parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "golden_dataset.json"
DEFAULT_RESULTS_DIR = _REPO_ROOT / "eval" / "results"

_PASSAGES_MARKER = "Retrieved passages:"
_ANSWER_MARKER = "LLM answer:"
_EXPANSION_ARTICLES_MARKER = "[query expansion] articles inférés : "
_EXPANSION_QUERY_MARKER = "[query expansion] expansion query  : "

RESET = "\033[0m"
BOLD = "\033[1m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"


# ---------------------------------------------------------------------------
# Subprocess runner
# ---------------------------------------------------------------------------

def _run_aria_ask(
    question: str,
    family: str | None,
    top_k: int,
    backend: str,
    expand_query: bool = False,
    alpha: float = 0.7,
    timeout: int = 120,
) -> str:
    cmd = ["aria-rag", "ask", question, "--top-k", str(top_k), "--backend", backend]
    if family:
        cmd += ["--family", family]
    if expand_query:
        cmd += ["--expand-query", "--alpha", str(alpha)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"aria-rag exited with code {result.returncode}")
    return result.stdout


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_output(raw: str) -> tuple[str, str, str, list[str]]:
    """Return (passages_block, llm_answer, expansion_query, inferred_articles) from aria-rag stdout."""
    passages = ""
    answer = ""
    expansion_query = ""
    inferred_articles: list[str] = []

    # Parse query expansion headers if present
    for line in raw.splitlines():
        if line.startswith(_EXPANSION_ARTICLES_MARKER):
            try:
                import ast
                inferred_articles = ast.literal_eval(line[len(_EXPANSION_ARTICLES_MARKER):].strip())
            except Exception:  # noqa: BLE001
                pass
        elif line.startswith(_EXPANSION_QUERY_MARKER):
            expansion_query = line[len(_EXPANSION_QUERY_MARKER):].strip()

    if _PASSAGES_MARKER in raw:
        start = raw.index(_PASSAGES_MARKER) + len(_PASSAGES_MARKER)
        rest = raw[start:]
        if _ANSWER_MARKER in rest:
            mid = rest.index(_ANSWER_MARKER)
            passages = rest[:mid].strip()
            answer = rest[mid + len(_ANSWER_MARKER):].strip()
        else:
            passages = rest.strip()
    else:
        passages = raw.strip()

    return passages, answer, expansion_query, inferred_articles


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_retrieval(passages: str, expected_articles: list[str]) -> tuple[float, list[str]]:
    if not expected_articles:
        return 1.0, []
    lower = passages.lower()
    missing = [art for art in expected_articles if art.lower() not in lower]
    return (len(expected_articles) - len(missing)) / len(expected_articles), missing


def _score_answer(answer: str, expected_keywords: list[str]) -> tuple[float, list[str]]:
    if not expected_keywords:
        return 1.0, []
    lower = answer.lower()
    missing = [kw for kw in expected_keywords if kw.lower() not in lower]
    return (len(expected_keywords) - len(missing)) / len(expected_keywords), missing


# ---------------------------------------------------------------------------
# Terminal display
# ---------------------------------------------------------------------------

def _color(score: float) -> str:
    if score >= 0.8:
        return _GREEN
    if score >= 0.5:
        return _YELLOW
    return _RED


def _bar(score: float, width: int = 8) -> str:
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled)


def _print_summary(results: list[dict[str, Any]]) -> None:
    id_w, q_w, score_w = 8, 52, 22
    total_w = id_w + q_w + score_w * 2
    sep = "─" * total_w

    title = "ARIA RAG — Résultats d'évaluation"
    print(f"\n{BOLD}{title:^{total_w}}{RESET}")
    print(sep)
    print(
        f"{BOLD}{'ID':<{id_w}}"
        f"{'Question (abrégée)':<{q_w}}"
        f"{'Retrieval':^{score_w}}"
        f"{'Answer':^{score_w}}{RESET}"
    )
    print(sep)

    for r in results:
        q = r["question"]
        q_short = (q[: q_w - 2] + "…") if len(q) > q_w - 1 else q
        rs, ans = r["retrieval_score"], r["answer_score"]
        # ANSI codes add invisible chars so we pad manually
        ret_cell = f"{_color(rs)}{rs:.0%} {_bar(rs)}{RESET}"
        ans_cell = f"{_color(ans)}{ans:.0%} {_bar(ans)}{RESET}"
        print(f"{r['id']:<{id_w}}{q_short:<{q_w}}{ret_cell:<{score_w + 10}}{ans_cell}")

        if r["missing_articles"]:
            print(f"{_DIM}  {'':>{id_w}}▸ articles manquants : {', '.join(r['missing_articles'])}{RESET}")
        if r["missing_keywords"]:
            print(f"{_DIM}  {'':>{id_w}}▸ keywords manquants  : {', '.join(r['missing_keywords'])}{RESET}")
        if r.get("error"):
            print(f"\033[91m  {'':>{id_w}}✗ erreur : {r['error']}{RESET}")

    print(sep)
    avg_ret = sum(r["retrieval_score"] for r in results) / len(results)
    avg_ans = sum(r["answer_score"] for r in results) / len(results)
    print(
        f"{BOLD}{'MOYENNE':<{id_w}}{'':>{q_w}}"
        f"{_color(avg_ret)}{avg_ret:.0%} {_bar(avg_ret)}{RESET}{BOLD}   "
        f"{_color(avg_ans)}{avg_ans:.0%} {_bar(avg_ans)}{RESET}"
    )
    print(sep)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _print_multi_comparison(runs: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """Print a retrieval comparison table for multiple named runs."""
    if not runs:
        return
    score_w = 18
    id_w, q_w = 8, 36
    total_w = id_w + q_w + score_w * len(runs)
    sep = "─" * total_w
    title = "Retrieval score — comparaison multi-run"
    print(f"\n{BOLD}{title:^{total_w}}{RESET}")
    print(sep)
    header = f"{BOLD}{'ID':<{id_w}}{'Question':<{q_w}}"
    for label, _ in runs:
        header += f"{label:^{score_w}}"
    print(header + RESET)
    print(sep)

    ids = [r["id"] for r in runs[0][1]]
    for uc_id in ids:
        scores = []
        question = ""
        for _, results in runs:
            row = next((r for r in results if r["id"] == uc_id), {})
            scores.append(row.get("retrieval_score", 0.0))
            if not question:
                question = row.get("question", "")
        q_short = (question[: q_w - 2] + "…") if len(question) > q_w - 1 else question
        line = f"{uc_id:<{id_w}}{q_short:<{q_w}}"
        base = scores[0]
        for i, s in enumerate(scores):
            cell = f"{_color(s)}{s:.0%} {_bar(s, 6)}{RESET}"
            if i > 0:
                delta = s - base
                arrow = f"{BOLD} {'↑' if delta > 0 else ('↓' if delta < 0 else '=')} {abs(delta):.0%}{RESET}"
                cell += arrow
            line += f"{cell:<{score_w + 10}}"
        print(line)
    print(sep)


def run_eval(
    dataset_path: Path | None = None,
    top_k: int = 8,
    backend: str = "ollama",
    ids: list[str] | None = None,
    results_dir: Path | None = None,
    timeout: int = 120,
    expand_query: bool = False,
    alpha: float = 0.7,
    baseline_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    ds_path = Path(dataset_path) if dataset_path else DEFAULT_DATASET
    out_dir = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR

    if not ds_path.exists():
        raise SystemExit(f"Dataset introuvable : {ds_path}")

    dataset: list[dict[str, Any]] = json.loads(ds_path.read_text(encoding="utf-8"))
    if ids:
        dataset = [uc for uc in dataset if uc["id"] in ids]
    if not dataset:
        raise SystemExit("Aucun use case à évaluer.")

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    mode = "avec query expansion" if expand_query else "sans query expansion"
    print(f"{BOLD}Évaluation sur {len(dataset)} use case(s) — backend={backend}, top_k={top_k}, {mode}{RESET}\n")

    for uc in dataset:
        uc_id = uc["id"]
        q_preview = uc["question"][:80] + ("…" if len(uc["question"]) > 80 else "")
        print(f"  {_DIM}→ {uc_id}{RESET} {q_preview}", flush=True)

        try:
            raw = _run_aria_ask(
                question=uc["question"],
                family=uc.get("family"),
                top_k=top_k,
                backend=backend,
                expand_query=expand_query,
                alpha=alpha,
                timeout=timeout,
            )
            passages, answer, expansion_query, inferred_articles = _parse_output(raw)
            ret_score, missing_arts = _score_retrieval(passages, uc.get("expected_articles", []))
            ans_score, missing_kws = _score_answer(answer, uc.get("expected_keywords", []))
            error = None
        except Exception as exc:  # noqa: BLE001
            passages, answer, expansion_query, inferred_articles = "", "", "", []
            ret_score, ans_score = 0.0, 0.0
            missing_arts = uc.get("expected_articles", [])
            missing_kws = uc.get("expected_keywords", [])
            error = str(exc)
            print(f"    {_RED}ERREUR : {exc}{RESET}", flush=True)

        results.append({
            "id": uc_id,
            "question": uc["question"],
            "complexity": uc.get("complexity", ""),
            "retrieval_score": round(ret_score, 4),
            "answer_score": round(ans_score, 4),
            "missing_articles": missing_arts,
            "missing_keywords": missing_kws,
            "alpha": alpha if expand_query else None,
            "expansion_query": expansion_query,
            "inferred_articles": inferred_articles,
            "raw_passages": passages,
            "raw_answer": answer,
            "error": error,
        })

    _print_summary(results)

    if baseline_results is not None:
        _print_comparison(baseline_results, results)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"results_{ts}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{_DIM}Résultats détaillés → {out_path}{RESET}\n")

    return results
