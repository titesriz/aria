"""Regression tests for the eval retrieval scorer (src/aria_rag/eval.py).

Guards against re-introducing the "code magnet" bug: a chunk whose raw
content happens to enumerate many article codes (e.g. an OAP-applicability
reference table) must not satisfy an expected item — of ANY type — unless
that's genuinely the hit's own metadata (section or source filename).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.eval import (
    _hit_satisfies,
    _normalize_code,
    _normalize_expectations,
    _parse_output,
    _score_retrieval,
)


def _hit(section: str | None = None, filename: str = "some_file.pdf") -> dict:
    return {"section": section, "filename": filename}


# Real chunk from data/index/chunks.json (id "REG2A1-1"): an OAP-applicability
# table in REG2A1.pdf whose *content* enumerates dozens of article codes
# ("UG.3.1.1 ; UG.3.2.2 ; UG.3.2.4 ; UG.3.3.8 ...") as table data, but whose
# actual *section* is "UG.1.4.1" — the article this table documents exceptions
# to. Under the old content-substring scorer, retrieving this single chunk
# fully validated CH-01 (score=1.0); it must not under the fixed scorer, for
# any of the three expectation types.
_MAGNET_CHUNK_SECTION = "UG.1.4.1"
_MAGNET_CHUNK_FILENAME = "REG2A1.pdf"
_MAGNET_CHUNK_CONTENT_EXCERPT = (
    "UG.1.4.1\nSous-section\nénonçant des\ndispositions\nparticulières\n"
    "(hors UG.1.4.1)\n...13e Masséna Bruneseau, non soumis "
    "UG.3.1.1 ; UG.3.2.2 ; UG.3.2.4 ; UG.3.3.8 X ..."
)
assert "UG.3.2" in _MAGNET_CHUNK_CONTENT_EXCERPT  # sanity: this is why the old scorer broke
assert "UG.3.3" in _MAGNET_CHUNK_CONTENT_EXCERPT

# CH-01 from eval/golden_dataset.json (legacy expected_articles form).
_CH01_EXPECTED_ARTICLES = ["UG.3.2", "UG.3.3", "UG.1.4.1"]


def _debug_block(hits: list[dict]) -> str:
    """Minimal `aria-rag ask --debug` stdout excerpt for the given hits (each
    a {"section", "filename"} dict), in the exact format cli.py prints. Only
    the "[N] filename | family" and "Page: X  Section: Y" lines matter to
    the parser.
    """
    lines = ["Debug — chunks retrieved:\n"]
    for i, hit in enumerate(hits, start=1):
        section = hit.get("section")
        lines.append(f"[{i}] {hit['filename']} | reglement_ecrit")
        lines.append("     FAISS: 0.1234  BM25: 12.3456  RRF: 0.01234")
        lines.append(f"     Page: 1  Section: {section if section is not None else 'n/a'}")
        lines.append("     'excerpt...'")
        lines.append("")
    lines.append("Retrieved passages:\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Magnet-chunk guard — all three expectation types
# ---------------------------------------------------------------------------

def test_magnet_chunk_does_not_satisfy_any_type_except_its_true_section():
    """The magnet chunk's raw content mentions UG.3.2/UG.3.3 as table data and
    sits in a file whose name has nothing to do with an unrelated expected
    document — none of that may leak into a match for ANY of the three
    types. Its one legitimate match is its own actual section, UG.1.4.1.
    """
    hit = _hit(section=_MAGNET_CHUNK_SECTION, filename=_MAGNET_CHUNK_FILENAME)

    assert not _hit_satisfies(hit, "article", "UG.3.2")
    assert not _hit_satisfies(hit, "article", "UG.3.3")
    assert not _hit_satisfies(hit, "section_label", "UG.3.2")
    assert not _hit_satisfies(hit, "section_label", "UG.3.3")
    assert not _hit_satisfies(hit, "document", "DG_E_HAUTEUR.pdf")
    assert not _hit_satisfies(hit, "document", "REG1.pdf")

    # Its true section is a legitimate match — the fix scopes matching to
    # metadata, it doesn't forbid this chunk from ever matching anything.
    assert _hit_satisfies(hit, "article", "UG.1.4.1")


def test_magnet_chunk_does_not_fully_validate_ch01_legacy_format():
    score, missing = _score_retrieval(
        [_hit(section=_MAGNET_CHUNK_SECTION, filename=_MAGNET_CHUNK_FILENAME)],
        _normalize_expectations({"expected_articles": _CH01_EXPECTED_ARTICLES}),
    )
    assert "UG.3.2" in missing
    assert "UG.3.3" in missing
    assert "UG.1.4.1" not in missing
    assert score == 1 / 3
    assert score != 1.0, "magnet chunk must not fully validate CH-01"


def test_magnet_chunk_does_not_fully_validate_ch01_new_format():
    """Same guarantee, expressed with explicit types (as CH-01 would be if
    migrated) — article, article, article. Type doesn't change the outcome
    here; the point is the new dict-based path is covered too.
    """
    ch01_expected = [
        {"type": "article", "value": "UG.3.2"},
        {"type": "article", "value": "UG.3.3"},
        {"type": "article", "value": "UG.1.4.1"},
    ]
    score, missing = _score_retrieval(
        [_hit(section=_MAGNET_CHUNK_SECTION, filename=_MAGNET_CHUNK_FILENAME)],
        ch01_expected,
    )
    assert set(missing) == {"UG.3.2", "UG.3.3"}
    assert score == 1 / 3


def test_magnet_chunk_parsed_from_real_debug_output():
    """End-to-end through _parse_output: a debug block whose only hit is the
    magnet chunk should parse to the right section+filename, and scoring
    against it must reproduce the same partial (not full) result.
    """
    raw = _debug_block([_hit(section=_MAGNET_CHUNK_SECTION, filename=_MAGNET_CHUNK_FILENAME)])
    _, _, _, _, _, hits = _parse_output(raw)

    assert hits == [{"filename": _MAGNET_CHUNK_FILENAME, "section": _MAGNET_CHUNK_SECTION}]
    score, missing = _score_retrieval(
        hits, _normalize_expectations({"expected_articles": _CH01_EXPECTED_ARTICLES})
    )
    assert score == 1 / 3
    assert set(missing) == {"UG.3.2", "UG.3.3"}


# ---------------------------------------------------------------------------
# "document" type
# ---------------------------------------------------------------------------

def test_document_type_matches_exact_basename():
    hit = _hit(section=None, filename="DG_E_HAUTEUR.pdf")
    assert _hit_satisfies(hit, "document", "DG_E_HAUTEUR.pdf")


def test_document_type_case_and_spacing_insensitive():
    hit = _hit(section=None, filename="DG_E_HAUTEUR.pdf")
    assert _hit_satisfies(hit, "document", "dg_e_hauteur.pdf")
    assert _hit_satisfies(hit, "document", "  DG_E_HAUTEUR.pdf  ")


def test_document_type_does_not_match_on_partial_filename_collision():
    """Exact basename equality, not substring — "REG1.pdf" must not match a
    hit filed under "REG10.pdf", and a value without its extension must not
    match either (that would just be a different kind of partial match).
    """
    hit = _hit(section=None, filename="REG10.pdf")
    assert not _hit_satisfies(hit, "document", "REG1.pdf")
    assert not _hit_satisfies(hit, "document", "REG1")

    exact_hit = _hit(section=None, filename="REG1.pdf")
    assert _hit_satisfies(exact_hit, "document", "REG1.pdf")


def test_document_type_matches_even_with_null_section():
    """A hit's filename always exists, even when it has no section (e.g. a
    reglement_graphique chunk) — document-type matching must not be blocked
    by section=None the way article/section_label matching is.
    """
    hit = _hit(section=None, filename="DG_E_HAUTEUR.pdf")
    assert _hit_satisfies(hit, "document", "DG_E_HAUTEUR.pdf")


# ---------------------------------------------------------------------------
# "article" / "section_label" — section=None guard, substring/prefix behavior
# ---------------------------------------------------------------------------

def test_section_match_required_not_content():
    """A code counts only when it's the hit's section — a hit whose section
    doesn't mention "Annexe X" at all leaves it missing, regardless of what
    its (unexamined) content might contain.
    """
    score, missing = _score_retrieval(
        [_hit(section="UG.2.2.3")],
        [{"type": "article", "value": "UG.2.2.3"}, {"type": "section_label", "value": "Annexe X"}],
    )
    assert score == 0.5
    assert missing == ["Annexe X"]


def test_null_section_never_matches_article_or_section_label():
    hits = [_hit(section=None), _hit(section=None)]
    score, missing = _score_retrieval(hits, [{"type": "article", "value": "UG.3.2"}])
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
    score, missing = _score_retrieval([_hit(section="UG.3.1.2")], [{"type": "article", "value": "UG.3.1"}])
    assert score == 1.0
    assert missing == []


def test_annexe_title_prefix_match():
    """expected items sometimes list a short form ("Annexe X") while the
    actual section is the full extracted title — must still match.
    """
    section = "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 5ÈME ARRONDISSEMENT"
    score, missing = _score_retrieval([_hit(section=section)], [{"type": "section_label", "value": "Annexe X"}])
    assert score == 1.0
    assert missing == []


def test_no_expected_items_is_trivially_satisfied():
    score, missing = _score_retrieval([_hit(section="anything")], [])
    assert score == 1.0
    assert missing == []


# ---------------------------------------------------------------------------
# Legacy vs. new dataset format
# ---------------------------------------------------------------------------

def test_normalize_expectations_legacy_form():
    uc = {"expected_articles": ["UG.3.2", "Annexe X"]}
    assert _normalize_expectations(uc) == [
        {"type": "article", "value": "UG.3.2"},
        {"type": "article", "value": "Annexe X"},
    ]


def test_normalize_expectations_new_form_passthrough():
    uc = {"expected": [{"type": "document", "value": "DG_E_HAUTEUR.pdf"}]}
    assert _normalize_expectations(uc) == uc["expected"]


def test_legacy_and_new_format_produce_identical_scores():
    """A case expressed the old way (expected_articles, all implicitly type
    "article") must score identically to the equivalent new-format case.
    """
    hits = [_hit(section="UG.3.1.2"), _hit(section=None, filename="other.pdf")]
    legacy_uc = {"expected_articles": ["UG.3.1", "UG.9.9"]}
    new_uc = {
        "expected": [
            {"type": "article", "value": "UG.3.1"},
            {"type": "article", "value": "UG.9.9"},
        ]
    }

    legacy_score, legacy_missing = _score_retrieval(hits, _normalize_expectations(legacy_uc))
    new_score, new_missing = _score_retrieval(hits, _normalize_expectations(new_uc))

    assert legacy_score == new_score
    assert legacy_missing == new_missing


def test_uc01_migrated_expectation_matches_real_plan_document():
    """UC-01's migrated expectation (see eval/golden_dataset.json): "Plan
    général des hauteurs" never appears in any chunk's section metadata
    (verified against the corpus — 0 matches), so it was migrated to type
    "document" against DG_E_HAUTEUR.pdf, the actual plan file (confirmed by
    its own extracted title text, "E - PLAN GÉNÉRAL DES HAUTEURS"). A hit on
    that file must satisfy it regardless of section.
    """
    uc01_expected = [
        {"type": "article", "value": "UG.3.2"},
        {"type": "document", "value": "DG_E_HAUTEUR.pdf"},
    ]
    hits = [_hit(section="UG.3.2.1"), _hit(section=None, filename="DG_E_HAUTEUR.pdf")]
    score, missing = _score_retrieval(hits, uc01_expected)
    assert score == 1.0
    assert missing == []
