"""Regression tests for loader.py's control-character stripping.

Guards against re-introducing the NUL-interleaving bug documented in the
extraction-fidelity audit: some fonts (mostly plan-tile legend labels in
reglement_graphique/annexes) are effectively 2-byte (UTF-16/CID) encoded but
get decoded as if 1-byte, leaving the zero high-byte of each code unit as a
literal U+0000 before EVERY character of the run — e.g. "\x00M\x00é\x00t"
for "Mét". U+0002 shows up the same way as part of a symbol-font marker
pair ("\x02\x8c").
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.loader import _normalize_font_artifacts

# Real examples from data/index/chunks.json (chunk_id ASUP4_2025_12_19-10/-11/-119,
# doc_family=annexes) before any fix — the NUL sits before EVERY character of
# the affected run, including the leading space and punctuation, never
# doubled, and never at a position that would otherwise have separated two
# words (words that end up concatenated, e.g. "ElémentaireEcole" below,
# are already concatenated with no separator in the raw extraction — a
# separate, pre-existing pypdf spacing limitation unrelated to this fix).
_NUL_EXAMPLES = [
    (
        "\x00 \x00M\x00é\x00t\x00a\x00l\x00l\x00o\x00s\n\x00T\x00h\x00.\n",
        " Métallos\nTh.\n",
    ),
    (
        "\x00M\x00u\x00n\x00.\n\x00E\x00l\x00é\x00m\x00e\x00n\x00t\x00a\x00i\x00r\x00e"
        "\x00E\x00c\x00o\x00l\x00e\n\x00P\x00.\x00M\x00.\x00I\x00.\n",
        "Mun.\nElémentaireEcole\nP.M.I.\n",
    ),
    (
        "\x00d\x00e\n\x00P\x00a\x00r\x00i\x00s\x00d\x00e\n\x00X\x00V\x00I\x00D\x00U\n",
        "de\nParisde\nXVIDU\n",
    ),
]

# Real example with U+0002 (chunk_id BBC.pdf legend), always paired with a
# trailing U+008C as a symbol-font marker — stripping U+0002 only (not
# U+008C, out of scope for this fix) still removes the noise character that
# would otherwise break BM25 tokenization.
_U0002_EXAMPLE = (
    "Bois de Boulogne - Centre\x02\x8c\x00 \x00B\x00o\x00i\x00s\x00 \x00d\x00e\x00 ",
    "Bois de Boulogne - Centre\x8c Bois de ",
)


def test_nul_interleaving_removed_and_reconstructs_correct_text():
    for raw, expected in _NUL_EXAMPLES:
        assert _normalize_font_artifacts(raw) == expected


def test_u0002_stripped():
    raw, expected = _U0002_EXAMPLE
    assert _normalize_font_artifacts(raw) == expected


def test_no_control_chars_remain():
    for raw, _ in _NUL_EXAMPLES:
        result = _normalize_font_artifacts(raw)
        assert "\x00" not in result
        assert "\x02" not in result


def test_removal_does_not_merge_distinct_words_beyond_pre_existing_extraction():
    """Stripping NULs must not merge two words that were genuinely separated
    in the raw extraction (i.e. it must not delete a NUL that was standing
    in for a space) — every NUL is strictly one-per-character, never a
    stand-alone separator, so word boundaries (spaces, newlines) that exist
    in the raw text survive untouched.
    """
    raw = "\x00A\x00B\x00C \x00D\x00E\x00F"
    result = _normalize_font_artifacts(raw)
    assert result == "ABC DEF"
    assert " " in result


def test_pua_bullet_normalization_still_works():
    """The control-char stripping is chained ahead of the existing PUA
    bullet substitution in the same normalization pass — must not disturb
    it.
    """
    result = _normalize_font_artifacts(f"item{chr(0xF0A0)}text")
    assert result == "item• text"


def test_empty_and_clean_text_unaffected():
    assert _normalize_font_artifacts("") == ""
    assert _normalize_font_artifacts("Rien à signaler.") == "Rien à signaler."
