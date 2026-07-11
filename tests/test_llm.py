"""Regression tests for the synthesis prompt's [N] marker system (src/aria_rag/llm.py).

extract_cited_markers() is the inverse of build_prompt()'s numbering — the
API layer uses it to compute each citation's `used` flag, so its
never-claim-a-false-negative contract (None, not False, when the signal
isn't reliable) is what api.py depends on.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.llm import build_prompt, extract_cited_markers
from aria_rag.retriever import SearchHit


def _hit(**overrides) -> SearchHit:
    defaults = dict(
        source_path="/docs/REG1.pdf",
        doc_family="reglement_ecrit",
        score=0.5,
        content="UG.1.1 some content",
        page=19,
        page_end=21,
        section="UG.1.1",
        faiss_score=0.04,
        bm25_score=12.3,
    )
    defaults.update(overrides)
    return SearchHit(**defaults)


# ---------------------------------------------------------------------------
# build_prompt — chunk numbering
# ---------------------------------------------------------------------------

def test_build_prompt_numbers_chunks_from_one_in_order():
    hits = [_hit(source_path="/docs/A.pdf"), _hit(source_path="/docs/B.pdf"), _hit(source_path="/docs/C.pdf")]
    prompt = build_prompt("question?", hits)
    assert "[1] Source: A.pdf" in prompt.user
    assert "[2] Source: B.pdf" in prompt.user
    assert "[3] Source: C.pdf" in prompt.user


def test_build_prompt_empty_hits_produces_empty_context():
    prompt = build_prompt("question?", [])
    assert "Context:\n" in prompt.user
    assert "[1]" not in prompt.user


# ---------------------------------------------------------------------------
# extract_cited_markers
# ---------------------------------------------------------------------------

def test_extract_cited_markers_finds_in_range_markers():
    answer = "Le retrait est de 3 mètres [1]. Voir aussi l'exception [2]."
    assert extract_cited_markers(answer, num_chunks=5) == {1, 2}


def test_extract_cited_markers_dedupes_repeated_marker():
    answer = "Règle [1]. Encore la règle [1]."
    assert extract_cited_markers(answer, num_chunks=5) == {1}


def test_extract_cited_markers_no_markers_returns_none():
    answer = "Le contexte ne précise pas cet aspect."
    assert extract_cited_markers(answer, num_chunks=5) is None


def test_extract_cited_markers_drops_out_of_range_but_keeps_valid():
    # 8 chunks retrieved, model cites [1] (valid) and [99] (hallucinated index)
    answer = "Vrai fait [1]. Fait douteux [99]."
    assert extract_cited_markers(answer, num_chunks=8) == {1}


def test_extract_cited_markers_all_out_of_range_falls_back_to_none():
    # Every marker present is unusable — as unreliable as no markers at all,
    # must not be reported as "cited nothing" (which would read as used=False).
    answer = "Fait improbable [42]."
    assert extract_cited_markers(answer, num_chunks=5) is None


def test_extract_cited_markers_ignores_non_numeric_brackets():
    # Chunk content sometimes contains "[Section: ...]" headers that could
    # leak into a quoted answer — must not be mistaken for a citation marker.
    answer = "D'après [Section: ANNEXE X], la hauteur est de 18 mètres [1]."
    assert extract_cited_markers(answer, num_chunks=3) == {1}


def test_extract_cited_markers_zero_chunks_never_matches():
    answer = "Une affirmation [1]."
    assert extract_cited_markers(answer, num_chunks=0) is None
