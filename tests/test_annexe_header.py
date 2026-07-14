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


# ---------------------------------------------------------------------------
# Overview-list boundary bug (2026-07-14 retest-debrief citation fix)
#
# REG1_MS1.pdf p.15-16 has a genuine, single paragraph enumerating all ten
# annexes by name ("• Annexe I – ... • Annexe II – ... ... • Annexe X –
# ..."), introducing what Tome 2 contains. Each bullet matches _ANNEXE_HEADER
# (well-formed uppercase title, same shape as a real header), and the LAST
# one ("Annexe X") used to win the bisect-based section lookup for
# everything from p.16 through p.40 — including Partie 2's Définitions
# glossary (chunk REG1_MS1-169: "prospect", "pièce principale",
# "réhabilitation"...), which has no header of its own in either pattern.
# ---------------------------------------------------------------------------

# Reconstructed shape of the real paragraph: several DIFFERENT annexe
# titles packed within ~200 chars of each other (the real corpus gap
# averages ~178 chars) -- the enumeration signature _drop_enumeration_runs
# detects. Followed by unrelated glossary-style content with no header of
# its own, mirroring the real Définitions section.
_OVERVIEW_LIST_TEXT = (
    "Le règlement est complété par les annexes suivantes :\n"
    "Annexe I – Liste des secteurs soumis à des dispositions particulières, avec\n"
    "l'indication des dispositions concernées.\n"
    "Annexe II – Liste des périmètres devant faire l'objet d'un projet.\n"
    "Annexe III – Liste des emplacements réservés aux voies, ouvrages publics.\n"
    "Annexe IV – Liste des périmètres de localisation d'équipements.\n"
    "\n\n"
    "Prospect\n"
    "En chaque point du périmètre de construction, le prospect désigne la mesure\n"
    "de l'horizontale perpendiculaire au périmètre en ce point.\n"
    "Réhabilitation\n"
    "Travaux visant à améliorer la performance d'une construction existante."
)


def test_overview_list_excluded_for_reg1_ms1():
    """The four distinct, tightly-packed titles are all dropped for
    REG1_MS1.pdf -- none can win the bisect lookup for the glossary text
    that follows, unlike before this fix.
    """
    matches = _titled_annexe_matches(_OVERVIEW_LIST_TEXT, "REG1_MS1.pdf")
    assert matches == []


def test_overview_list_not_touched_for_other_files():
    """The enumeration exclusion is deliberately scoped to REG1_MS1.pdf --
    applying it corpus-wide during this fix's own dry-run mislabeled a
    REG2A1_MS1.pdf chunk whose content genuinely was Annexe III as Annexe
    II instead (different document structure, not audited). Other files'
    pre-existing behavior — including this same overview-list shape, if it
    occurs there — must be untouched.
    """
    matches = _titled_annexe_matches(_OVERVIEW_LIST_TEXT, "REG2A1_MS1.pdf")
    assert len(matches) == 4


def test_cross_tome_reference_excluded_for_reg1_ms1():
    """A header-shaped match that names itself as living in the OTHER tome
    ("Annexe IV du tome 2 du règlement écrit...") is a cross-reference in
    running prose, never a section this document opens itself.
    """
    text = "les équipements de logistique urbaine liés à l'Annexe IV du tome 2 du règlement écrit, ou mentionnés par une OAP."
    assert _titled_annexe_matches(text, "REG1_MS1.pdf") == []


def test_isolated_real_annexe_header_still_recognized_for_reg1_ms1():
    """The REG1_MS1.pdf-specific exclusions only fire on enumerations and
    cross-tome references — a single, genuine annexe header (not part of a
    tight run of several different titles) must still be recognized.
    """
    text = "Fin de section.\nA NNEXE V : LISTE DES EMPLACEMENTS RÉSERVÉS\nSuite du texte."
    matches = _titled_annexe_matches(text, "REG1_MS1.pdf")
    assert len(matches) == 1
    assert matches[0].group(0).startswith("A NNEXE V")


def test_glossary_style_content_carries_no_annexe_match_after_fix():
    """Regression guard for the actual reported bug, phrased the way the
    retest debrief reported it: in the reconstructed REG1_MS1.pdf shape,
    zero annexe matches survive at all, so a bisect-based section lookup
    for the glossary text ("Prospect", "Réhabilitation"...) can never
    resolve to any "Annexe ..." value again.

    Full None-or-Définitions-family attribution additionally depends on
    the article side not separately winning instead -- REG1_MS1.pdf has a
    SEPARATE, deliberately untouched ToC-pollution bug on that side (see
    SESSION_STATE.md, 2026-07-14) which still resolves the real corpus's
    equivalent chunk to an article code, not None. That's out of scope for
    this fix; this test only guards the annexe side actually reported.
    """
    matches = _titled_annexe_matches(_OVERVIEW_LIST_TEXT, "REG1_MS1.pdf")
    assert matches == []
