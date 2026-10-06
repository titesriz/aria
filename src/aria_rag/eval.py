"""Evaluation module — runs aria-rag ask end-to-end and scores against a golden dataset."""
from __future__ import annotations

import json
import random
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from aria_rag.config import Settings
from aria_rag.judge import judge_answer

_REPO_ROOT = Path(__file__).parent.parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "golden_dataset.json"
DEFAULT_RESULTS_DIR = _REPO_ROOT / "eval" / "results"
DEFAULT_EXPANSION_CACHE = _REPO_ROOT / "eval" / "expansion_cache.json"

# Cases whose reponse_attendue contains an unresolved doctrinal branch
# ("DOCTRINE À TRANCHER", "à vérifier", "À RECONSTRUIRE" -- see
# golden_dataset.json) -- their judge scores are indicative only until
# Charline resolves the branch. Reported separately, never silently folded
# into the headline mean.
INDICATIVE_ONLY_CASE_IDS = {"CH-04", "UC-16", "UC-04"}

# The 4 cases Charline calibrates the judge against (see
# export_calibration_sheet). Picked for this task, not derived from data.
CALIBRATION_CASE_IDS = ["CH-03", "CH-01", "UC-02", "UC-05"]

# Fairness (2026-09-22 PoC task): a reasonable system prompt a competent user
# would write -- French urban-planning expert, Paris PLU bioclimatique scope,
# encouraged to cite articles, encouraged to flag uncertainty rather than
# invent a figure. No corpus, no retrieved context, no mention of ARIA.
BASELINE_NO_CORPUS_SYSTEM_PROMPT = (
    "Tu es un expert français en droit de l'urbanisme, spécialisé dans le Plan "
    "Local d'Urbanisme (PLU) bioclimatique de Paris et le Code de la "
    "Construction et de l'Habitation (CCH). Des architectes te posent des "
    "questions réglementaires précises sur des projets à Paris. Réponds en "
    "français, de manière structurée et précise. Cite les articles "
    "réglementaires pertinents quand tu les connais (par exemple UG.3.2, ou "
    "un article du CCH). Si tu n'es pas certain d'une règle, d'un seuil ou "
    "d'un chiffre, dis-le clairement plutôt que d'inventer une valeur."
)

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


_ARTICLE_RE = re.compile(r'^[A-Z]{1,8}(?:\.\d+)+(?:\s+\d+°)?$')
_PAREN_RE = re.compile(r'\([^)]*\)')
_GUILLEMET_RE = re.compile(r'«[^»]*»')
_PAGE_REF_RE = re.compile(r'\bp\.\s*\d+\b')
_RESOURCE_SPLIT_RE = re.compile(r'[,+]')


def parse_expected_resources(raw: str) -> list[dict[str, str]]:
    """Parse the "Articles / ressources attendus" free-text field into
    canonical [{"type", "value"}, ...] items, by rule rather than by
    matching known strings.

    Each comma- or "+"-separated part is stripped of annotation —
    parentheticals "(...)", guillemet quotes «...», and page refs "p.232"
    — then classified: if what remains matches an article-code shape
    (zone-prefix letters, dot-separated numbers, optional trailing "N°"
    clause, e.g. "UG.3.1.2", "UG.1.4.1 3°"), it's type "article"; otherwise
    it's a free-form resource label ("Annexe X", "Figure 6", "Plan des
    hauteurs"), type "resource".
    """
    if not raw:
        return []
    items = []
    for part in _RESOURCE_SPLIT_RE.split(raw):
        cleaned = _GUILLEMET_RE.sub(' ', part)
        cleaned = _PAREN_RE.sub(' ', cleaned)
        cleaned = _PAGE_REF_RE.sub(' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if not cleaned:
            continue
        item_type = "article" if _ARTICLE_RE.match(cleaned) else "resource"
        items.append({"type": item_type, "value": cleaned})
    return items


def _normalize_expectations(uc: dict[str, Any]) -> list[dict[str, str]]:
    """Return uc's expected-retrieval items in canonical [{"type", "value"}, ...]
    form. Supports the legacy `expected_articles: [str, ...]` field, the legacy
    `expected: [{"type", "value"}, ...]` field, and the Notion-v2 export's
    `articles_attendus` field — a free-text string (scripts/export_golden_cases.py's
    current output; parsed by parse_expected_resources) or, for older exports,
    a list of plain strings each treated as type "article". A case is expected
    to use exactly one of these three.
    """
    if "expected" in uc:
        return uc["expected"]
    if "articles_attendus" in uc:
        raw = uc["articles_attendus"]
        if isinstance(raw, str):
            return parse_expected_resources(raw)
        return [{"type": "article", "value": v} for v in raw]
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
    - "resource": free-form labels from parse_expected_resources (e.g.
      "Annexe X", "Figure 6") — these can equally be the hit's section
      (if the corpus tags a chunk with that label) or a substring of its
      filename (if the label names the whole document/annexe rather than
      a section within it), so both are checked.
    """
    if item_type == "document":
        return _normalize_code(value) == _normalize_code(hit["filename"])
    if item_type == "resource":
        if hit["filename"] and _normalize_code(value) in _normalize_code(hit["filename"]):
            return True
        return hit["section"] is not None and _normalize_code(value) in _normalize_code(hit["section"])
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


def _judge_case(uc: dict[str, Any], answer: str, settings: Settings) -> dict[str, Any] | None:
    """Wraps judge_answer with this golden case's reponse_attendue /
    critere_reussite. Returns None (not judged) rather than calling the
    judge on an empty/absent answer -- e.g. --no-llm runs, or a case whose
    aria-rag ask subprocess errored before producing an answer.
    """
    if not answer:
        return None
    reponse_attendue = uc.get("reponse_attendue", "")
    critere_reussite = uc.get("critere_reussite", "")
    if not reponse_attendue and not critere_reussite:
        return None
    return judge_answer(uc["question"], answer, reponse_attendue, critere_reussite, settings)


def _failed_forbidden_checks(judge: dict[str, Any] | None) -> list[str]:
    if not judge or judge.get("judge_error"):
        return []
    return [c["check"] for c in judge.get("checks", []) if c.get("type") == "forbidden" and not c.get("pass")]


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


def _print_result_table(results: list[dict[str, Any]], failed_ids: list[str]) -> None:
    id_w, q_w, score_w = 8, 52, 22
    total_w = id_w + q_w + score_w * 2
    sep = "─" * total_w

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
        ans_cell = f"{_color(ans)}{ans:.0%} {_bar(ans)}{RESET}" if ans is not None else f"{_DIM}n/a{RESET}"
        id_label = (r["id"] + "!") if r["id"] in failed_ids else r["id"]
        print(f"{id_label:<{id_w}}{q_short:<{q_w}}{ret_cell:<{score_w + 10}}{ans_cell}")

        if r["id"] in failed_ids:
            print(f"{_RED}  {'':>{id_w}}✗ expansion failed : scored on fallback-to-original retrieval{RESET}")
        if r["missing_articles"]:
            print(f"{_DIM}  {'':>{id_w}}▸ articles manquants : {', '.join(r['missing_articles'])}{RESET}")
        failed_forbidden = _failed_forbidden_checks(r.get("judge"))
        if failed_forbidden:
            print(f"{_RED}  {'':>{id_w}}▸ checks interdits échoués : {'; '.join(failed_forbidden)}{RESET}")
        if r.get("judge") and r["judge"].get("judge_error"):
            print(f"{_YELLOW}  {'':>{id_w}}⚠ judge_error : le juge n'a pas produit de JSON valide (2 tentatives){RESET}")
        if r.get("error"):
            print(f"\033[91m  {'':>{id_w}}✗ erreur : {r['error']}{RESET}")

    print(sep)
    avg_ret = sum(r["retrieval_score"] for r in results) / len(results)
    scored = [r["answer_score"] for r in results if r["answer_score"] is not None]
    avg_ans_str = f"{_color(sum(scored) / len(scored))}{sum(scored) / len(scored):.0%} {_bar(sum(scored) / len(scored))}{RESET}" if scored else f"{_DIM}n/a{RESET}"
    print(
        f"{BOLD}{'MOYENNE':<{id_w}}{'':>{q_w}}"
        f"{_color(avg_ret)}{avg_ret:.0%} {_bar(avg_ret)}{RESET}{BOLD}   "
        f"{avg_ans_str}"
    )
    print(sep)


def _print_summary(results: list[dict[str, Any]]) -> None:
    """Print certified (validated=true) and pending (everything else) results
    as two SEPARATE tables with their own averages — a pending case's score
    must never be blended into a "headline" number presented as certified.
    `validated` is absent entirely on datasets that predate the Notion-v2
    export (e.g. golden_dataset_LEGACY.json); those cases fall into "pending"
    by the same rule (missing == not certified), never into "certified" by
    default.
    """
    id_w, q_w, score_w = 8, 52, 22
    total_w = id_w + q_w + score_w * 2

    failed_ids = [r["id"] for r in results if r.get("expansion_status") == "failed"]
    if failed_ids:
        print(
            f"\n{_RED}{BOLD}⚠ QUERY EXPANSION FAILED for {len(failed_ids)} case(s): "
            f"{', '.join(failed_ids)} — scored against a silent fallback to the "
            f"original query, not a genuine expansion. See ERROR-level logs above.{RESET}"
        )

    certified = [r for r in results if r.get("validated") is True]
    pending = [r for r in results if r.get("validated") is not True]

    print(f"\n{BOLD}{'ARIA RAG — Résultats d’évaluation — CERTIFIÉ (validated=true)':^{total_w}}{RESET}")
    if certified:
        _print_result_table(certified, failed_ids)
    else:
        print(
            f"{_YELLOW}(0 cas certifié — aucun score certifié disponible tant que "
            f"'Validé Charline' n'est pas coché dans Notion pour au moins un cas){RESET}"
        )

    print(
        f"\n{_DIM}{BOLD}"
        f"{'— cas EN ATTENTE de validation (validated=false / non renseigné) — informatif uniquement, jamais une certification —':^{total_w}}"
        f"{RESET}"
    )
    if pending:
        _print_result_table(pending, failed_ids)
    else:
        print(f"{_DIM}(aucun cas en attente){RESET}")


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
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    from aria_rag.config import load_settings
    settings = settings or load_settings()
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
            judge = _judge_case(uc, answer, settings)
            ans_score = judge["final_score"] if judge and not judge["judge_error"] else None
            error = None
        except Exception as exc:  # noqa: BLE001
            passages, answer, expansion_query, inferred_articles = "", "", "", []
            # We don't know whether expansion itself failed or the whole
            # subprocess did — mark it failed too rather than silently
            # reporting no signal, so --strict-expansion still catches it.
            expansion_status = "failed" if expand_query else None
            ret_score, ans_score, judge = 0.0, None, None
            missing_arts = [item["value"] for item in _normalize_expectations(uc)]
            error = str(exc)
            print(f"    {_RED}ERREUR : {exc}{RESET}", flush=True)

        results.append({
            "id": uc_id,
            "question": uc["question"],
            "complexity": uc.get("complexity", ""),
            "validated": uc.get("validated"),
            "retrieval_score": round(ret_score, 4),
            "answer_score": round(ans_score, 4) if ans_score is not None else None,
            "judge": judge,
            "missing_articles": missing_arts,
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


# ---------------------------------------------------------------------------
# No-corpus baseline (PoC validation instrument, 2026-09-22)
# ---------------------------------------------------------------------------

def _answer_claude_no_corpus(question: str, settings: Settings) -> str:
    """EVAL-ONLY synthesis call for the no-corpus baseline. Deliberately its
    own function, not llm.answer_with_claude: that function builds a
    RAG-context prompt (llm.build_prompt) and stays reserved/unreachable
    from production per the 2026-09-22 backend-removal cleanup; this one
    sends the question alone, with BASELINE_NO_CORPUS_SYSTEM_PROMPT, no
    retrieved context, and no [N]-marker citation convention (there is no
    numbered context to cite).
    """
    import anthropic

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=120)
    full_text: list[str] = []
    try:
        with client.messages.stream(
            model=settings.claude_model,
            max_tokens=settings.num_predict,
            system=BASELINE_NO_CORPUS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        ) as stream:
            for text in stream.text_stream:
                full_text.append(text)
    except anthropic.AnthropicError as exc:
        raise RuntimeError(f"Claude request failed ({type(exc).__name__}). Please try again.") from exc

    text = "".join(full_text).strip()
    if not text:
        raise RuntimeError("Claude returned an empty response.")
    return text


def load_baseline_answers_from_file(path: Path) -> dict[str, str]:
    """{case_id: answer_text} — an alternate input for run_baseline_no_corpus
    when a live ANTHROPIC_API_KEY isn't available (or a baseline run was
    sourced by other means, e.g. a manual chat session) but the SAME judge
    and comparison pipeline should still score it. Not a second-class path:
    the resulting results are structurally identical to a live run's.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object of {{case_id: answer_text}}, got {type(data).__name__}")
    return data


def run_baseline_no_corpus(
    dataset: list[dict[str, Any]],
    settings: Settings,
    answers_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Run (or load) the no-corpus baseline for every case in `dataset`,
    then judge each answer with the SAME judge as ARIA's run (_judge_case).
    Result shape is deliberately parallel to run_eval's per-case dict
    (id/question/raw_answer/judge/answer_score/error) minus retrieval-only
    fields (retrieval_score, missing_articles, expansion_*) that don't apply
    — there is no retrieval in this condition.
    """
    provided = load_baseline_answers_from_file(answers_file) if answers_file else {}
    results: list[dict[str, Any]] = []
    print(f"{BOLD}Baseline sans corpus (Claude, {settings.claude_model}) sur {len(dataset)} cas{RESET}\n")

    for uc in dataset:
        uc_id = uc["id"]
        print(f"  {_DIM}→ [baseline] {uc_id}{RESET} {uc['question'][:80]}", flush=True)
        answer, judge, error = "", None, None
        try:
            if uc_id in provided:
                answer = provided[uc_id]
            elif answers_file is not None:
                raise RuntimeError(f"Pas de réponse fournie pour {uc_id} dans {answers_file}")
            else:
                answer = _answer_claude_no_corpus(uc["question"], settings)
            judge = _judge_case(uc, answer, settings)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            print(f"    {_RED}ERREUR : {exc}{RESET}", flush=True)

        results.append({
            "id": uc_id,
            "question": uc["question"],
            "raw_answer": answer,
            "judge": judge,
            "answer_score": judge["final_score"] if judge and not judge["judge_error"] else None,
            "error": error,
        })
    return results


# ---------------------------------------------------------------------------
# Comparison (ARIA vs. no-corpus baseline)
# ---------------------------------------------------------------------------

def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def build_comparison_rows(aria_results: list[dict[str, Any]], baseline_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id_base = {r["id"]: r for r in baseline_results}
    rows = []
    for a in aria_results:
        uc_id = a["id"]
        b = by_id_base.get(uc_id, {})
        aria_score, base_score = a.get("answer_score"), b.get("answer_score")
        rows.append({
            "id": uc_id,
            "question": a["question"],
            "indicative_only": uc_id in INDICATIVE_ONLY_CASE_IDS,
            "retrieval_score": a.get("retrieval_score"),
            "aria_answer_score": aria_score,
            "baseline_answer_score": base_score,
            "delta": (aria_score - base_score) if (aria_score is not None and base_score is not None) else None,
            "aria_failed_forbidden": _failed_forbidden_checks(a.get("judge")),
            "baseline_failed_forbidden": _failed_forbidden_checks(b.get("judge")),
        })
    return rows


def print_poc_comparison(rows: list[dict[str, Any]]) -> None:
    id_w, q_w, score_w = 8, 40, 13
    total_w = id_w + q_w + score_w * 3 + 10
    sep = "─" * total_w

    def _cell(score: float | None) -> str:
        if score is None:
            return f"{_DIM}n/a{RESET}"
        return f"{_color(score)}{score:.0%}{RESET}"

    print(sep)
    print(f"{BOLD}{'ID':<{id_w}}{'Question':<{q_w}}{'Retrieval':^{score_w}}{'ARIA':^{score_w}}{'Baseline':^{score_w}}{'Δ':^10}{RESET}")
    print(sep)
    for r in rows:
        q_short = (r["question"][: q_w - 2] + "…") if len(r["question"]) > q_w - 1 else r["question"]
        flag = " *" if r["indicative_only"] else ""
        delta = r["delta"]
        delta_str = f"{'+' if delta > 0 else ''}{delta:.0%}" if delta is not None else "n/a"
        print(
            f"{r['id'] + flag:<{id_w}}{q_short:<{q_w}}"
            f"{_cell(r['retrieval_score']):^{score_w + 9}}"
            f"{_cell(r['aria_answer_score']):^{score_w + 9}}"
            f"{_cell(r['baseline_answer_score']):^{score_w + 9}}"
            f"{delta_str:^10}"
        )
        if r["aria_failed_forbidden"]:
            print(f"{_RED}  {'':>{id_w}}▸ ARIA — checks interdits échoués : {'; '.join(r['aria_failed_forbidden'])}{RESET}")
        if r["baseline_failed_forbidden"]:
            print(f"{_RED}  {'':>{id_w}}▸ Baseline — checks interdits échoués : {'; '.join(r['baseline_failed_forbidden'])}{RESET}")
    print(sep)

    all_ids = [r["id"] for r in rows]
    excl_ids = [r["id"] for r in rows if not r["indicative_only"]]
    for label, ids_subset in (("12/12 cas", all_ids), (f"{len(excl_ids)}/12 cas (hors CH-04/UC-16/UC-04)", excl_ids)):
        subset = [r for r in rows if r["id"] in ids_subset]
        mean_aria = _mean([r["aria_answer_score"] for r in subset])
        mean_base = _mean([r["baseline_answer_score"] for r in subset])
        mean_ret = _mean([r["retrieval_score"] for r in subset])
        print(
            f"{BOLD}MOYENNE {label:<40}{RESET}"
            f"retrieval={_cell(mean_ret)}  ARIA={_cell(mean_aria)}  baseline={_cell(mean_base)}"
        )
    print(f"{_DIM}* CH-04/UC-16/UC-04 : réponse attendue contient une branche non tranchée "
          f"(DOCTRINE À TRANCHER / à vérifier / À RECONSTRUIRE) — score indicatif uniquement.{RESET}")
    print(sep)


def save_poc_comparison(
    aria_results: list[dict[str, Any]],
    baseline_results: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    out_dir: Path,
    baseline_source: str,
) -> Path:
    """Full outputs (both systems' answers + judge JSON), timestamped, per
    CLAUDE.md's "every fix ships with a before/after eval run in
    eval/results/" convention -- this is that record for the PoC comparison.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "baseline_source": baseline_source,  # "live_api" or the answers-file path
        "indicative_only_case_ids": sorted(INDICATIVE_ONLY_CASE_IDS),
        "comparison_rows": rows,
        "aria_results": aria_results,
        "baseline_results": baseline_results,
    }
    out_path = out_dir / f"poc_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Calibration sheet for Charline (blind A/B, judge verdicts shown)
# ---------------------------------------------------------------------------

def export_calibration_sheet(
    aria_results: list[dict[str, Any]],
    baseline_results: list[dict[str, Any]],
    out_dir: Path,
    case_ids: list[str] | None = None,
    seed: int = 20260922,
) -> tuple[Path, Path]:
    """Writes a blind A/B markdown sheet (sheet_path) for Charline to mark
    where she disagrees with the judge, plus a SEPARATE key file (key_path)
    mapping A/B back to {aria, baseline_no_corpus} per case -- not shown on
    the sheet itself, so the review stays genuinely blind. Per-case A/B
    assignment is randomized with a fixed seed (reproducible, not
    hand-picked to flatter either system).
    """
    case_ids = case_ids or CALIBRATION_CASE_IDS
    by_id_aria = {r["id"]: r for r in aria_results}
    by_id_base = {r["id"]: r for r in baseline_results}
    rng = random.Random(seed)

    key: dict[str, dict[str, str]] = {}
    lines = [
        "# Feuille de calibration du juge",
        "",
        "Pour chaque cas : lisez la question et les deux réponses (A/B, non "
        "identifiées), puis le verdict du juge pour chacune. Indiquez votre "
        "accord/désaccord sur chaque check et sur le score final dans les "
        "cases `[ ]` (cochez `[x]` si vous êtes en désaccord avec le juge, "
        "et notez pourquoi).",
        "",
        "---",
        "",
    ]
    for uc_id in case_ids:
        a, b = by_id_aria.get(uc_id), by_id_base.get(uc_id)
        if a is None or b is None:
            continue
        systems = [("aria", a), ("baseline_no_corpus", b)]
        rng.shuffle(systems)
        (label_A, data_A), (label_B, data_B) = systems
        key[uc_id] = {"A": label_A, "B": label_B}

        lines.append(f"## {uc_id}")
        lines.append("")
        lines.append(f"**Question :** {a['question']}")
        lines.append("")
        for label, data in (("A", data_A), ("B", data_B)):
            lines.append(f"### Réponse {label}")
            lines.append("")
            lines.append(data.get("raw_answer") or "*(pas de réponse)*")
            lines.append("")
            judge = data.get("judge")
            score = data.get("answer_score")
            score_str = f"{score:.0%}" if score is not None else "n/a (judge_error)"
            lines.append(f"**Verdict du juge — Réponse {label} — score final : {score_str}**")
            lines.append("")
            if judge and not judge.get("judge_error"):
                lines.append("| Désaccord ? | Check | Type | Résultat | Justification du juge |")
                lines.append("|---|---|---|---|---|")
                for c in judge.get("checks", []):
                    result = "✅ pass" if c.get("pass") else "❌ fail"
                    lines.append(f"| `[ ]` | {c.get('check','')} | {c.get('type','')} | {result} | {c.get('justification','')} |")
                lines.append("")
                lines.append(f"`[ ]` Désaccord sur le **substance_score** ({judge.get('substance_score')}) — commentaire : ____________")
                lines.append("")
                lines.append(f"*Rationale du juge : {judge.get('rationale','')}*")
            else:
                lines.append("*(judge_error — le juge n'a pas produit de verdict exploitable pour cette réponse)*")
            lines.append("")
        lines.append("---")
        lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sheet_path = out_dir / f"calibration_sheet_{ts}.md"
    key_path = out_dir / f"calibration_key_{ts}.json"
    sheet_path.write_text("\n".join(lines), encoding="utf-8")
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    return sheet_path, key_path


# ---------------------------------------------------------------------------
# Orchestrator — `aria-rag eval --baseline-no-corpus`
# ---------------------------------------------------------------------------

def run_poc_validation(
    dataset_path: Path | None = None,
    top_k: int = 8,
    ids: list[str] | None = None,
    results_dir: Path | None = None,
    timeout: int = 120,
    alpha: float = 0.5,
    answers_file: Path | None = None,
    refresh_expansions: bool = False,
    settings: Settings | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path, Path, Path]:
    """Runs ARIA (Ollama synthesis, query expansion on — ARIA's best
    configured condition, matching how the baseline is also given a fair
    prompt rather than a handicapped one) and the no-corpus baseline on the
    same dataset, judges both with the same judge, prints the comparison,
    saves full outputs, and exports the calibration sheet. Returns
    (aria_results, baseline_results, comparison_path, sheet_path, key_path).
    """
    from aria_rag.config import load_settings
    settings = settings or load_settings()
    out_dir = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR
    ds_path = Path(dataset_path) if dataset_path else DEFAULT_DATASET
    dataset: list[dict[str, Any]] = json.loads(ds_path.read_text(encoding="utf-8"))
    if ids:
        dataset = [uc for uc in dataset if uc["id"] in ids]

    print(f"{BOLD}=== 1/2 : ARIA (RAG, Ollama, query expansion) ==={RESET}\n")
    aria_results = run_eval(
        dataset_path=dataset_path, top_k=top_k, backend="ollama", ids=ids,
        results_dir=results_dir, timeout=timeout, expand_query=True, alpha=alpha,
        no_llm=False, refresh_expansions=refresh_expansions, settings=settings,
    )

    print(f"\n{BOLD}=== 2/2 : Baseline sans corpus (Claude) ==={RESET}\n")
    baseline_results = run_baseline_no_corpus(dataset, settings, answers_file=answers_file)

    rows = build_comparison_rows(aria_results, baseline_results)
    print()
    print_poc_comparison(rows)

    baseline_source = str(answers_file) if answers_file else "live_api"
    comparison_path = save_poc_comparison(aria_results, baseline_results, rows, out_dir, baseline_source)
    print(f"\n{_DIM}Comparaison complète → {comparison_path}{RESET}")

    sheet_path, key_path = export_calibration_sheet(aria_results, baseline_results, out_dir)
    print(f"{_DIM}Feuille de calibration (Charline) → {sheet_path}{RESET}")
    print(f"{_DIM}Clé A/B (ne pas montrer à Charline) → {key_path}{RESET}")
    print(
        f"\n{_YELLOW}⚠ en attente de calibration Charline — ce résultat n'est PAS validé "
        f"tant que la feuille de calibration n'a pas été relue.{RESET}"
    )

    return aria_results, baseline_results, comparison_path, sheet_path, key_path
