"""Regression tests for the eval retrieval scorer (src/aria_rag/eval.py).

Guards against re-introducing the "code magnet" bug: a chunk whose raw
content happens to enumerate many article codes (e.g. an OAP-applicability
reference table) must not satisfy an expected article unless that code is
actually the chunk's own section.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.eval import _normalize_code, _parse_output, _score_retrieval

# Real chunk from data/index/chunks.json (id "REG2A1-1"): an OAP-applicability
# table in REG2A1.pdf whose *content* enumerates dozens of article codes
# ("UG.3.1.1 ; UG.3.2.2 ; UG.3.2.4 ; UG.3.3.8 ...") as table data, but whose
# actual *section* is "UG.1.4.1" — the article this table documents exceptions
# to. Under the old content-substring scorer, retrieving this single chunk
# fully validated CH-01 (score=1.0); it must not under the fixed scorer.
_MAGNET_CHUNK_SECTION = "UG.1.4.1"
_MAGNET_CHUNK_CONTENT_EXCERPT = (
    "UG.1.4.1\nSous-section\nénonçant des\ndispositions\nparticulières\n"
    "(hors UG.1.4.1)\n...13e Masséna Bruneseau, non soumis "
    "UG.3.1.1 ; UG.3.2.2 ; UG.3.2.4 ; UG.3.3.8 X ..."
)
assert "UG.3.2" in _MAGNET_CHUNK_CONTENT_EXCERPT  # sanity: this is why the old scorer broke
assert "UG.3.3" in _MAGNET_CHUNK_CONTENT_EXCERPT

# CH-01 from eval/golden_dataset.json.
_CH01_EXPECTED_ARTICLES = ["UG.3.2", "UG.3.3", "UG.1.4.1"]


def _debug_block(sections: list[str | None]) -> str:
    """Minimal `aria-rag ask --debug` stdout excerpt for the given per-hit
    sections, in the exact format cli.py prints. Only the "Page: X
    Section: Y" line matters to the parser.
    """
    lines = ["Debug — chunks retrieved:\n"]
    for i, section in enumerate(sections, start=1):
        lines.append(f"[{i}] some_file.pdf | reglement_ecrit")
        lines.append("     FAISS: 0.1234  BM25: 12.3456  RRF: 0.01234")
        lines.append(f"     Page: 1  Section: {section if section is not None else 'n/a'}")
        lines.append("     'excerpt...'")
        lines.append("")
    lines.append("Retrieved passages:\n")
    return "\n".join(lines)


def test_magnet_chunk_does_not_fully_validate_ch01():
    """The documented REG2A1 OAP reference table chunk (section=UG.1.4.1,
    content mentions UG.3.2/UG.3.3 only as table data) must not satisfy
    CH-01's UG.3.2 or UG.3.3 expectations — only its own section, UG.1.4.1.
    """
    score, missing = _score_retrieval([_MAGNET_CHUNK_SECTION], _CH01_EXPECTED_ARTICLES)

    assert "UG.3.2" in missing
    assert "UG.3.3" in missing
    assert "UG.1.4.1" not in missing
    assert score == 1 / 3
    assert score != 1.0, "magnet chunk must not fully validate CH-01"


def test_magnet_chunk_parsed_from_real_debug_output():
    """End-to-end through _parse_output: a debug block whose only hit is the
    magnet chunk should parse to a single section, and scoring against it
    must reproduce the same partial (not full) result.
    """
    raw = _debug_block([_MAGNET_CHUNK_SECTION])
    _, _, _, _, hit_sections = _parse_output(raw)

    assert hit_sections == ["UG.1.4.1"]
    score, missing = _score_retrieval(hit_sections, _CH01_EXPECTED_ARTICLES)
    assert score == 1 / 3
    assert set(missing) == {"UG.3.2", "UG.3.3"}


def test_section_match_required_not_content():
    """A code counts only when it's the hit's section — a hit whose section
    doesn't mention "Annexe X" at all leaves it missing, regardless of what
    its (unexamined) content might contain.
    """
    score, missing = _score_retrieval(["UG.2.2.3"], ["UG.2.2.3", "Annexe X"])
    assert score == 0.5
    assert missing == ["Annexe X"]


def test_null_section_never_matches():
    """Chunks with section=None must never satisfy any expected article."""
    score, missing = _score_retrieval([None, None], ["UG.3.2"])
    assert score == 0.0
    assert missing == ["UG.3.2"]


def test_normalize_code_handles_case_dots_spacing():
    assert _normalize_code("UG.3.1.1") == _normalize_code("ug.3.1.1")
    assert _normalize_code("UG.3.1.1") == _normalize_code("  UG.3.1.1  ")
    assert _normalize_code("UG.3.1.1") == _normalize_code("UG. 3 .1 .1")
    assert _normalize_code("Annexe X") == _normalize_code("annexe x")
    # dots are separators, not noise — must not be stripped entirely
    assert _normalize_code("UG.3.1") != _normalize_code("UG.31")


def test_prefix_relationship_preserved():
    """expected_articles frequently list a parent code (e.g. "UG.3.1") while
    chunks are tagged with the more specific sub-article section (e.g.
    "UG.3.1.2") — normalized substring-in-section must still satisfy this,
    keeping the old content-substring behavior's useful side effect without
    its false-positive cost.
    """
    score, missing = _score_retrieval(["UG.3.1.2"], ["UG.3.1"])
    assert score == 1.0
    assert missing == []


def test_annexe_title_prefix_match():
    """expected_articles sometimes list a short form ("Annexe X") while the
    actual section is the full extracted title — must still match.
    """
    section = "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 5ÈME ARRONDISSEMENT"
    score, missing = _score_retrieval([section], ["Annexe X"])
    assert score == 1.0
    assert missing == []


def test_no_expected_articles_is_trivially_satisfied():
    score, missing = _score_retrieval(["anything"], [])
    assert score == 1.0
    assert missing == []
