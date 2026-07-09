"""Evaluation module — runs aria-rag ask end-to-end and scores against a golden dataset."""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parent.parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "golden_dataset.json"
DEFAULT_RESULTS_DIR = _REPO_ROOT / "eval" / "results"
DEFAULT_EXPANSION_CACHE = _REPO_ROOT / "eval" / "expansion_cache.json"

_PASSAGES_MARKER = "Retrieved passages:"
_ANSWER_MARKER = "LLM answer:"
_EXPANSION_ARTICLES_MARKER = "[query expansion] articles inférés : "
_EXPANSION_QUERY_MARKER = "[query expansion] expansion query  : "
_EXPANSION_STATUS_MARKER = "[query expansion] status : "
# Two lines per retrieved hit, printed by `aria-rag ask --debug` (cli.py), in a
# fixed 1:1:1 order per hit — used to score against each hit's *metadata*
# (section, source filename) instead of its raw content. See _score_retrieval.
_DEBUG_HIT_HEADER = re.compile(r'^\[\d+\]\s+(.+?)\s+\|\s+\S+\s*$', re.MULTILINE)
_DEBUG_SECTION_LINE = re.compile(r'^\s*Page:\s*\S+\s+Section:\s*(.*)$', re.MULTILINE)

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
    alpha: float = 0.5,
    no_llm: bool = False,
    timeout: int = 120,
    refresh_expansions: bool = False,
) -> str:
    cmd = ["aria-rag", "ask", question, "--top-k", str(top_k), "--backend", backend, "--debug"]
    if family:
        cmd += ["--family", family]
    if expand_query:
        # Cache expansion results — CPU-backed Ollama can vary by a token even
        # at temperature=0+seed, which would otherwise make eval scores a
        # partly-random draw across runs.
        cmd += ["--expand-query", "--alpha", str(alpha), "--expansion-cache", str(DEFAULT_EXPANSION_CACHE)]
        if refresh_expansions:
            cmd += ["--refresh-expansions"]
    if no_llm:
        cmd += ["--no-llm"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"aria-rag exited with code {result.returncode}")
    return result.stdout


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_output(raw: str) -> tuple[str, str, str, list[str], str | None, list[dict[str, str | None]]]:
    """Return (passages_block, llm_answer, expansion_query, inferred_articles,
    expansion_status, hits) from aria-rag stdout. `hits` is one {"section",
    "filename"} dict per retrieved hit, in rank order, parsed from the --debug
    output: "section" comes from the "Page: X Section: Y" line (None where the
    hit has no section, printed as "n/a" by cli.py); "filename" from the
    "[N] filename | family" header line (always present — a hit always has a
    source file). Requires --debug to have been passed to `aria-rag ask`.
    expansion_status is None when --expand-query wasn't passed (no
    "[query expansion] status" line to parse); otherwise "ok" / "empty" /
    "failed" per query_expansion.expand_query.
    """
    passages = ""
    answer = ""
    expansion_query = ""
    inferred_articles: list[str] = []
    expansion_status: str | None = None
    filenames = [m.group(1).strip() for m in _DEBUG_HIT_HEADER.finditer(raw)]
    sections: list[str | None] = [
        None if m.group(1).strip() == "n/a" else m.group(1).strip()
        for m in _DEBUG_SECTION_LINE.finditer(raw)
    ]
    hits: list[dict[str, str | None]] = [
        {"filename": fname, "section": section}
        for fname, section in zip(filenames, sections)
    ]

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
        elif line.startswith(_EXPANSION_STATUS_MARKER):
            expansion_status = line[len(_EXPANSION_STATUS_MARKER):].strip()

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

    return passages, answer, expansion_query, inferred_articles, expansion_status, hits


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _normalize_code(s: str) -> str:
    """Case/dot/whitespace-insensitive form for comparing an article code against
    a chunk's section title. Collapses all whitespace and strips a trailing period
    ("UG.3.1.1." vs "UG.3.1.1") without touching internal dots — those are
    meaningful separators (stripping them would conflate e.g. UG.3.1 and UG.31).
    """
    return re.sub(r'\s+', '', s).lower().rstrip('.')


def _normalize_expectations(uc: dict[str, Any]) -> list[dict[str, str]]:
    """Return uc's expected-retrieval items in canonical [{"type", "value"}, ...]
    form. Supports both the legacy `expected_articles: [str, ...]` field (every
    item treated as type "article" — unchanged behavior for any case that hasn't
    been migrated to the new format) and the new `expected: [{"type", "value"},
    ...]` field. A case must use one or the other, not both.
    """
    if "expected" in uc:
        return uc["expected"]
    return [{"type": "article", "value": v} for v in uc.get("expected_articles", [])]


def _hit_satisfies(hit: dict[str, str | None], item_type: str, value: str) -> bool:
    """Whether one retrieved hit's *metadata* (never its raw content — this is
    the invariant that killed the magnet-chunk artifact, and it must hold for
    every expectation type) satisfies one expected item.

    - "document": exact (normalized) equality against the hit's source filename.
      Coarse-grained fallback for labels that name a whole document/plan rather
      than an article — a hit's filename always exists, so section=None hits
      can still satisfy this. Exact equality, not substring: a substring check
      on a value without its extension would let "REG1" wrongly match
      "REG10.pdf"; requiring the full normalized basename avoids that.
    - "article" / "section_label": normalized substring against the hit's
      `section` metadata. Both types check the same field — "section_label"
      exists as a distinct, documented category for prose labels (e.g. "Plan
      général des hauteurs") and annexe/document titles rather than bare
      article codes, but the match mechanics are identical to "article".
      section=None hits never satisfy either.
    """
    if item_type == "document":
        return _normalize_code(value) == _normalize_code(hit["filename"])
    if hit["section"] is None:
        return False
    return _normalize_code(value) in _normalize_code(hit["section"])


def _score_retrieval(
    hits: list[dict[str, str | None]], expected: list[dict[str, str]]
) -> tuple[float, list[str]]:
    """Fraction of `expected` items satisfied by at least one hit, regardless of
    type — a hit's raw content is never consulted (see _hit_satisfies).

    Substring (not exact) matching for "article"/"section_label" intentionally
    keeps the "expected code is a prefix of a more specific section" relationship
    the old content-substring check relied on (expected articles are frequently
    a parent code like "UG.3.1" while chunks are tagged with the specific
    sub-article, e.g. section="UG.3.1.2") — normalized substring against section
    preserves that, while no longer matching a code that merely appears somewhere
    in an unrelated chunk's body text (e.g. a reference table listing dozens of
    article codes as data).
    """
    if not expected:
        return 1.0, []
    missing = [
        item["value"] for item in expected
        if not any(_hit_satisfies(hit, item["type"], item["value"]) for hit in hits)
    ]
    return (len(expected) - len(missing)) / len(expected), missing


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

    failed_ids = [r["id"] for r in results if r.get("expansion_status") == "failed"]
    if failed_ids:
        print(
            f"\n{_RED}{BOLD}⚠ QUERY EXPANSION FAILED for {len(failed_ids)} case(s): "
            f"{', '.join(failed_ids)} — scored against a silent fallback to the "
            f"original query, not a genuine expansion. See ERROR-level logs above.{RESET}"
        )

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
        id_label = (r["id"] + "!") if r["id"] in failed_ids else r["id"]
        print(f"{id_label:<{id_w}}{q_short:<{q_w}}{ret_cell:<{score_w + 10}}{ans_cell}")

        if r["id"] in failed_ids:
            print(f"{_RED}  {'':>{id_w}}✗ expansion failed : scored on fallback-to-original retrieval{RESET}")
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


def _make_results_path(out_dir: Path, ts: str | None = None) -> Path:
    """results_{timestamp}_{short-uuid}.json — the uuid suffix guarantees two
    writes never collide even when they land in the same wall-clock second,
    whether from concurrent processes or two fast-sequential run_eval() calls
    (e.g. the baseline + expansion pair `--expand-query` makes) within one.
    """
    ts = ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    return out_dir / f"results_{ts}_{uuid.uuid4().hex[:8]}.json"


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
    alpha: float = 0.5,
    no_llm: bool = False,
    baseline_results: list[dict[str, Any]] | None = None,
    refresh_expansions: bool = False,
    strict_expansion: bool = False,
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
                no_llm=no_llm,
                timeout=timeout,
                refresh_expansions=refresh_expansions,
            )
            passages, answer, expansion_query, inferred_articles, expansion_status, hits = _parse_output(raw)
            ret_score, missing_arts = _score_retrieval(hits, _normalize_expectations(uc))
            ans_score, missing_kws = _score_answer(answer, uc.get("expected_keywords", []))
            error = None
        except Exception as exc:  # noqa: BLE001
            passages, answer, expansion_query, inferred_articles = "", "", "", []
            # We don't know whether expansion itself failed or the whole
            # subprocess did — mark it failed too rather than silently
            # reporting no signal, so --strict-expansion still catches it.
            expansion_status = "failed" if expand_query else None
            ret_score, ans_score = 0.0, 0.0
            missing_arts = [item["value"] for item in _normalize_expectations(uc)]
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
            "expansion_status": expansion_status,
            "raw_passages": passages,
            "raw_answer": answer,
            "error": error,
        })

    _print_summary(results)

    if baseline_results is not None:
        _print_comparison(baseline_results, results)

    out_path = _make_results_path(out_dir)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{_DIM}Résultats détaillés → {out_path}{RESET}\n")

    if strict_expansion:
        failed_ids = [r["id"] for r in results if r.get("expansion_status") == "failed"]
        if failed_ids:
            raise SystemExit(
                f"--strict-expansion: query expansion failed for {len(failed_ids)} case(s): "
                f"{', '.join(failed_ids)}"
            )

    return results
