"""Regression tests for _ANNEXE_HEADER (src/aria_rag/indexer.py).

Guards against re-introducing the false-positive bug documented in the
ingestion audit: a bare `re.IGNORECASE` flag neutralized the uppercase
character classes meant to distinguish a real title header ("ANNEXE V :
LISTE...", "Annexe V : Liste...") from an inline lowercase prose mention
("l'annexe I du tome 2 du règlement écrit indique..."), letting 81/4438
reglement_ecrit chunks pick up a garbage mid-sentence section value.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.indexer import _ANNEXE_HEADER, _titled_annexe_matches

# Reconstructed prose contexts for 5 of the 23 documented false-positive
# section strings (data/index/chunks.json, doc_family="reglement_ecrit",
# section starting with lowercase "annexe"). The bug: these produced a
# Chunk.section equal (or close) to the trailing substring below.
_FALSE_POSITIVE_CONTEXTS = [
    "Les zones sont réparties comme indiqué dans l'annexe I du tome 2 du règlement écrit indique les références des lots.",
    "Cette liste figure dans l'annexe IX du tome 2 du règlement écrit.",
    "L'annexe X du tome 2 du règlement écrit recense par adresse les protections patrimoniales concernées.",
    "Le bâtiment constitue une annexe du PLU.",
    "Le zonage est décrit dans l'annexe I du tome 2 du règlement écrit comme suit.",
]

# 5 real true-positive headers (verbatim Chunk.section values from
# data/index/chunks.json) spanning all three casings actually present in
# the corpus: fully uppercase, the "A NNEXE" split-by-pypdf artifact, and
# Title Case (REG2A1.pdf uses this variant, unlike the REG2A10 series).
_TRUE_POSITIVE_HEADERS = [
    "A NNEXE V : LISTE DES EMPLACEMENTS RÉSERVÉS EN VUE DE LA RÉALISATION DE certains types de logements.",
    "A NNEXE VI : LISTE DES ESPACES VERTS PROTÉGÉS (UG.4.3.4, UGSU.4.3.3, UV.4.3.3)",
    "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 5ÈME ARRONDISSEMENT\nType Localisation",
    "Annexe V : Liste des emplacements réservés en vue de la réalisation de certains",
    "Annexe IV : Périmètres de localisation d'équipements publics.",
]


def test_false_positives_no_longer_match():
    for text in _FALSE_POSITIVE_CONTEXTS:
        assert _ANNEXE_HEADER.search(text) is None, f"must not match: {text!r}"


def test_true_positives_still_match():
    for text in _TRUE_POSITIVE_HEADERS:
        m = _ANNEXE_HEADER.search(text)
        assert m is not None, f"must still match: {text!r}"
        assert m.start() == 0


def test_case_variants_all_recognized_as_the_annexe_keyword():
    """The keyword itself (ANNEXE / Annexe / A NNEXE) stays case-insensitive
    — only the surrounding title-starter classes became case-sensitive.
    """
    for keyword in ("ANNEXE", "Annexe", "A NNEXE"):
        text = f"{keyword} VII : Liste des sites de protection de l'agriculture urbaine"
        m = _ANNEXE_HEADER.search(text)
        assert m is not None, f"must match keyword variant: {keyword!r}"


def test_all_lowercase_keyword_never_matches():
    """Never-capitalized "annexe" is the false-positive signature — must
    fail regardless of what (even uppercase-looking) text follows, since a
    genuine title header is never written with a lowercase keyword.
    """
    text = "annexe VII : Liste des sites de protection de l'agriculture urbaine"
    assert _ANNEXE_HEADER.search(text) is None


def test_inline_cross_reference_exclusion_unaffected():
    """_titled_annexe_matches excludes "(Annexe IV)"-style parenthetical
    cross-references — untouched by this fix, verified still works.
    """
    text = "Ces emplacements sont mentionnés (Annexe IV : Périmètres de localisation)."
    raw_matches = list(_ANNEXE_HEADER.finditer(text))
    assert len(raw_matches) == 1  # the regex itself still matches...
    assert _titled_annexe_matches(text) == []  # ...but the cross-ref filter drops it


def test_titled_annexe_matches_keeps_real_headers():
    text = "Fin de section.\nA NNEXE V : LISTE DES EMPLACEMENTS RÉSERVÉS\nSuite du texte."
    matches = _titled_annexe_matches(text)
    assert len(matches) == 1
    assert matches[0].group(0).startswith("A NNEXE V")
