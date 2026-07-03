"""Diagnostic script — traces WHY an expected chunk fails to rank in top-k.

Read-only: loads the existing index and reuses the production scoring
function (retriever._compute_all_scores) so ranks/scores are exactly what
the real pipeline computes — this script does not reimplement retrieval
math, it just runs it with fetch_k = full corpus size so every chunk's true
rank is visible, not just the top-80 candidates a top_k=8 query would fetch.

No pipeline code is modified. Output is written to
scripts/audit_ranking_report.md.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import dataclass
from typing import Callable

import numpy as np

import httpx

from aria_rag.config import load_settings
from aria_rag.indexer import Chunk
from aria_rag.query_expansion import _SYSTEM_PROMPT, _parse_articles
from aria_rag.retriever import _compute_all_scores, load_index

_EXPANSION_TIMEOUT = 60  # pipeline hardcodes 15s, too short for CPU Ollama — diagnostic only, not a pipeline change


def expand_query_patient(question: str, ollama_host: str, ollama_model: str) -> tuple[str, str, list[str]]:
    """Same contract as query_expansion.expand_query, with a longer timeout for CPU inference.

    Duplicates only the network call (reuses the real _parse_articles/_SYSTEM_PROMPT) —
    this script doesn't modify query_expansion.py, it just can't rely on its 15s timeout
    for diagnostic purposes on a CPU-bound Ollama instance.
    """
    payload = {
        "model": ollama_model, "prompt": question, "system": _SYSTEM_PROMPT,
        "stream": False, "temperature": 0,
    }
    url = f"{ollama_host.rstrip('/')}/api/generate"
    try:
        response = httpx.post(url, json=payload, timeout=_EXPANSION_TIMEOUT)
        response.raise_for_status()
        articles = _parse_articles(response.json().get("response", ""))
    except Exception as exc:  # noqa: BLE001
        print(f"  [expansion error, patient timeout] {exc}")
        articles = []
    if not articles:
        return question, "", []
    return question, " ".join(articles), articles

OUT_PATH = Path(__file__).resolve().parent / "audit_ranking_report.md"


@dataclass
class Case:
    id: str
    query: str
    family_filter: list[str] | None
    expected_predicate: Callable[[Chunk], bool]
    expected_label: str
    use_expansion: bool  # whether the losing eval run used --expand-query
    expected_articles: list[str]  # literal strings eval.py's _score_retrieval checks for
    alpha: float = 0.5


def _eval_replica_check(top8: list[tuple[int, float]], chunks: list[Chunk], expected_articles: list[str]) -> dict[str, bool]:
    """Exactly replicates eval.py's _score_retrieval: format_hits shortens each
    chunk to 500 chars (textwrap.shorten), joins them, then does a lowercase
    substring check per expected article string. This is what the eval
    harness actually scores against — not the same thing as "is the right
    chunk in the top-8," since a truncated or continuation chunk may rank
    #1 without literally containing the article code string.
    """
    passages = "\n\n".join(
        textwrap.shorten(chunks[i].content, width=500, placeholder="...") for i, _ in top8
    )
    lower = passages.lower()
    return {art: (art.lower() in lower) for art in expected_articles}


def _rank_of(target_idx: int, scores: dict[int, float]) -> tuple[int, float]:
    """1-indexed rank of target_idx within scores, plus its own score.

    Chunks absent from `scores` (e.g. FAISS returned it with score -> not
    present because fetch_k didn't cover it) are treated as rank = len+1,
    worse than everything present. With fetch_k=full corpus this shouldn't
    happen, but the fallback keeps the report honest if it ever does.
    """
    own = scores.get(target_idx)
    if own is None:
        return len(scores) + 1, float("nan")
    better = sum(1 for v in scores.values() if v > own)
    return better + 1, own


def _top_n(scores: dict[int, float], chunks: list[Chunk], n: int, exclude: int | None = None) -> list[tuple[int, float]]:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if exclude is not None:
        ranked = [(i, s) for i, s in ranked if i != exclude]
    return ranked[:n]


def _fmt_chunk(idx: int, chunks: list[Chunk], score: float | None = None) -> str:
    c = chunks[idx]
    fname = Path(c.source_path).name
    score_str = f"{score:.4f}" if score is not None else "n/a"
    preview = c.content[:70].replace("\n", " ")
    return f"idx={idx} score={score_str} [{c.doc_family}] {fname} p{c.page} sec={c.section!r} — {preview!r}"


def _categorize(faiss_rank: int, bm25_rank: int, rrf_rank: int, corpus_size: int, top_k: int = 8) -> str:
    good = max(50, corpus_size // 200)  # "ranks reasonably" threshold, scales with corpus
    faiss_ok = faiss_rank <= good
    bm25_ok = bm25_rank <= good
    if rrf_rank <= top_k:
        return "not a loss (ranks in top-{})".format(top_k)
    if faiss_ok and bm25_ok:
        return "fusion artifact (both retrievers place it respectably, but not enough to beat competitors strong on BOTH axes within RRF's harmonic-style merge)"
    if faiss_ok and not bm25_ok:
        return "lexical dilution (FAISS finds it, BM25 buries it under lexically similar competitors)"
    if bm25_ok and not faiss_ok:
        return "semantic miss (BM25 finds it, embedding similarity doesn't distinguish it)"
    return "dual weak signal (poor on both FAISS and BM25 — neither retriever surfaces it)"


def analyze_case(
    case: Case,
    model, faiss_index, bm25, chunks: list[Chunk],
) -> str:
    lines: list[str] = []
    lines.append(f"## {case.id}: {case.expected_label}")
    lines.append("")
    lines.append(f"Query: *{case.query}*")
    lines.append("")

    if case.family_filter:
        allowed = {i for i, c in enumerate(chunks) if c.doc_family in case.family_filter}
    else:
        allowed = set(range(len(chunks)))
    corpus_size = len(allowed)

    candidates = [i for i in allowed if case.expected_predicate(chunks[i])]
    if not candidates:
        lines.append(f"**No chunk in the index matches the expected-content predicate.** Cannot audit.")
        lines.append("")
        return "\n".join(lines)

    # fetch_k must cover the FULL FAISS index (all families), not just `allowed` —
    # the index spans the whole corpus, so a family-filtered fetch_k undercounts:
    # it only guarantees `len(allowed)` results total across ALL families, of
    # which just a fraction land in `allowed` after filtering. Full corpus size
    # guarantees every allowed chunk gets a real score, not a fallback placeholder.
    fetch_k = len(chunks)

    # --- baseline (no expansion) ---
    rrf0, faiss0, bm250 = _compute_all_scores(case.query, model, faiss_index, bm25, chunks, allowed, fetch_k)
    # pick whichever candidate ranks best under baseline RRF, as "the" expected chunk
    target = min(candidates, key=lambda i: _rank_of(i, rrf0)[0])
    lines.append(f"Expected chunk (best-ranked among {len(candidates)} candidate(s) matching the predicate):")
    lines.append(f"- `{_fmt_chunk(target, chunks)}`")
    lines.append("")

    faiss_rank0, faiss_score0 = _rank_of(target, faiss0)
    bm25_rank0, bm25_score0 = _rank_of(target, bm250)
    rrf_rank0, rrf_score0 = _rank_of(target, rrf0)

    lines.append(f"**Baseline (no query expansion), corpus={corpus_size}:**")
    lines.append(f"- FAISS: rank {faiss_rank0}/{corpus_size}, score {faiss_score0:.4f}")
    lines.append(f"- BM25:  rank {bm25_rank0}/{corpus_size}, score {bm25_score0:.4f}")
    lines.append(f"- RRF:   rank {rrf_rank0}/{corpus_size}, score {rrf_score0:.5f}")
    lines.append("")

    top8_baseline = _top_n(rrf0, chunks, 8)
    lines.append("Top-8 RRF winners (baseline) — who beats it:")
    for rank, (i, s) in enumerate(top8_baseline, start=1):
        marker = "  <-- EXPECTED" if i == target else ""
        lines.append(f"{rank}. `{_fmt_chunk(i, chunks, s)}`{marker}")
    lines.append("")

    category = _categorize(faiss_rank0, bm25_rank0, rrf_rank0, corpus_size)
    lines.append(f"**Category (baseline): {category}**")
    lines.append("")

    eval_check0 = _eval_replica_check(top8_baseline, chunks, case.expected_articles)
    lines.append(f"**eval.py-equivalent scoring (baseline, {{article: substring_found}}):** {eval_check0}")
    lines.append("")

    if not case.use_expansion:
        return "\n".join(lines)

    # --- with expansion ---
    settings = load_settings()
    original_q, expansion_q, inferred_articles = expand_query_patient(
        case.query, ollama_host=settings.ollama_host, ollama_model=settings.ollama_model,
    )
    lines.append(f"**With query expansion (alpha={case.alpha}):**")
    lines.append(f"- Inferred articles: {inferred_articles}")
    lines.append(f"- Expansion query string: `{expansion_q!r}`")
    lines.append("")

    if not expansion_q.strip():
        lines.append("Expansion produced no articles — falls back to baseline query, no change.")
        lines.append("")
        return "\n".join(lines)

    rrf_exp, faiss_exp, bm25_exp = _compute_all_scores(expansion_q, model, faiss_index, bm25, chunks, allowed, fetch_k)
    combined = {
        i: case.alpha * rrf0.get(i, 0.0) + (1 - case.alpha) * rrf_exp.get(i, 0.0)
        for i in set(rrf0) | set(rrf_exp)
    }
    rank_exp_rrf, _ = _rank_of(target, rrf_exp)
    rank_combined, score_combined = _rank_of(target, combined)

    lines.append(f"- Expansion-query-alone RRF rank for expected chunk: {rank_exp_rrf}/{corpus_size}")
    lines.append(f"- Combined (alpha-weighted) rank: {rank_combined}/{corpus_size}, score {score_combined:.5f}")
    lines.append(f"- Baseline-only rank was: {rrf_rank0}/{corpus_size}")
    delta = rrf_rank0 - rank_combined
    direction = "IMPROVED" if delta > 0 else ("WORSENED" if delta < 0 else "unchanged")
    lines.append(f"- Expansion effect: rank {direction} by {abs(delta)} positions")
    lines.append("")

    top8_combined = _top_n(combined, chunks, 8)
    lines.append("Top-8 combined winners (with expansion) — who beats it:")
    for rank, (i, s) in enumerate(top8_combined, start=1):
        marker = "  <-- EXPECTED" if i == target else ""
        lines.append(f"{rank}. `{_fmt_chunk(i, chunks, s)}`{marker}")
    lines.append("")

    eval_check_exp = _eval_replica_check(top8_combined, chunks, case.expected_articles)
    lines.append(f"**eval.py-equivalent scoring (with expansion, {{article: substring_found}}):** {eval_check_exp}")
    lines.append("")

    if rank_combined > rrf_rank0 and rank_exp_rrf > rrf_rank0:
        exp_category = "expansion misdirection (expansion query itself ranks the expected chunk worse than the original query did)"
    elif rank_combined > rrf_rank0:
        exp_category = "expansion dilution (expansion query alone isn't worse, but alpha-blending with it still pulls the combined rank down)"
    else:
        exp_category = "expansion neutral-to-positive (not the cause of the loss)"
    lines.append(f"**Category (expansion contribution): {exp_category}**")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    settings = load_settings()
    model, faiss_index, bm25, chunks = load_index(settings)
    print(f"Loaded index: {len(chunks)} chunks", flush=True)

    def sec_prefix(prefix: str) -> Callable[[Chunk], bool]:
        return lambda c: c.section is not None and c.section.startswith(prefix)

    def content_all(*needles: str) -> Callable[[Chunk], bool]:
        return lambda c: all(n in c.content for n in needles)

    def content_and_section(needle: str, section_needle: str) -> Callable[[Chunk], bool]:
        return lambda c: needle in c.content and c.section is not None and section_needle in c.section

    cases = [
        Case(
            id="UC-01",
            query="Je cherche la hauteur maximale autorisée pour une construction neuve en zone UG dans le 11e arrondissement de Paris, le terrain est en secteur DG5.",
            family_filter=["reglement_ecrit"],
            expected_predicate=content_and_section("Plan général des hauteurs", "UG.3.2"),
            expected_label='Article UG.3.2.1 — "Plan général des hauteurs" subsection',
            use_expansion=True,
            expected_articles=["UG.3.2", "Plan général des hauteurs"],
        ),
        Case(
            id="UC-02",
            query="Je cherche la règle de retrait par rapport à la voie publique pour une construction en zone UG dans le 15e arrondissement de Paris. Le projet est à l'alignement ou nécessite-t-il un retrait minimum de 3 mètres ?",
            family_filter=["reglement_ecrit"],
            expected_predicate=sec_prefix("UG.3.1.1"),
            expected_label="Article UG.3.1.1",
            use_expansion=True,
            expected_articles=["UG.3.1.1", "UG.3.1"],
        ),
        Case(
            id="UC-04",
            query="Je cherche si un changement de destination de bureaux vers hôtel est autorisé en zone UG dans le 8e arrondissement de Paris. Quelles sont les conditions réglementaires applicables ?",
            family_filter=["reglement_ecrit"],
            expected_predicate=sec_prefix("UG.1.3"),
            expected_label="Article UG.1.3",
            use_expansion=True,
            expected_articles=["UG.1.3", "UG.1"],
        ),
        Case(
            id="Table-1er",
            query="quels emplacements réservés pour logements dans le 1er arrondissement",
            family_filter=["reglement_ecrit"],
            expected_predicate=content_all("19 rue", "Argenteuil", "1er"),
            expected_label="Annexe V — 1er arrondissement address row (rue d'Argenteuil)",
            use_expansion=False,
            expected_articles=[],
        ),
        Case(
            id="Table-1er-unfiltered",
            query="quels emplacements réservés pour logements dans le 1er arrondissement",
            family_filter=None,  # no --family — matches the production /ask path (Figma frontend)
            expected_predicate=content_all("19 rue", "Argenteuil", "1er"),
            expected_label="Annexe V — 1er arrondissement address row (rue d'Argenteuil), FULL CORPUS",
            use_expansion=False,
            expected_articles=[],
        ),
    ]

    report_parts = ["# Retrieval ranking audit\n"]
    for case in cases:
        print(f"Analyzing {case.id}...", flush=True)
        report_parts.append(analyze_case(case, model, faiss_index, bm25, chunks))

    report = "\n".join(report_parts)
    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"Report written to {OUT_PATH}")


if __name__ == "__main__":
    main()
