"""Regression tests for the table chunker (src/aria_rag/indexer.py:
chunk_text_by_table and friends) — the CH-06 fix. REG2A1_MS1.pdf and
REG2A10_*.pdf are compact tables (arrondissement/address/reference rows,
protected-building entries), not article prose; chunk_text_by_article's
char-window fallback used to cut a row apart at an arbitrary chunk_size
boundary, destroying it (CH-06: 2/17 addresses retrieved from Annexe V
instead of ~17).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.indexer import (
    TABLE_ROW_MAX_LEN,
    _merge_same_header_spans,
    _real_annexe_headers,
    chunk_text_by_table,
)

CHUNK_SIZE = 1200

# ---------------------------------------------------------------------------
# _real_annexe_headers: ToC exclusion
# ---------------------------------------------------------------------------

# Reconstructed shape of REG2A1_MS1.pdf's front-matter ToC (9 entries, each
# "Annexe N : <title> ....... <page>") followed by the real content-opening
# header (no dot leader) and its table.
_TOC_THEN_CONTENT = (
    "Annexe I : Liste des secteurs soumis à des dispositions particulières ........... 3\n"
    "Annexe II : Liste des périmètres devant faire l'objet d'un projet ............... 15\n"
    "Annexe III : Liste des emplacements réservés aux voies ........................... 30\n"
    "\n\n"
    "Annexe I : Liste des secteurs soumis à des\n"
    "dispositions particulières\n"
    "Le document graphique indique les secteurs concernés.\n"
)


def test_toc_entries_excluded():
    matches = _real_annexe_headers(_TOC_THEN_CONTENT)
    assert len(matches) == 1
    assert matches[0].group(0).startswith("Annexe I : Liste des secteurs")
    assert "...." not in matches[0].group(0)


def test_repeated_running_header_not_excluded():
    """A running page header (identical text, no dots) repeating on every
    page of the same section is a real, if redundant, header — must not be
    mistaken for a ToC entry."""
    text = (
        "A NNEXE V : LISTE DES EMPLACEMENTS RÉSERVÉS\n"
        "1er 15 rue d'Argenteuil LS 100-100\n"
        "A NNEXE V : LISTE DES EMPLACEMENTS RÉSERVÉS\n"
        "2e 54-56 rue d'Aboukir LS 35-35\n"
    )
    matches = _real_annexe_headers(text)
    assert len(matches) == 2
    assert all(m.group(0).startswith("A NNEXE V") for m in matches)


def test_inline_cross_reference_excluded():
    text = "Ces emplacements sont mentionnés (Annexe IV : Périmètres de localisation)."
    assert _real_annexe_headers(text) == []


# ---------------------------------------------------------------------------
# _merge_same_header_spans
# ---------------------------------------------------------------------------

def test_merge_collapses_identical_repeats():
    text = (
        "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 1ER ARRONDISSEMENT\n"
        "BP 3 rue d'Alger Description.\n"
        "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 1ER ARRONDISSEMENT\n"
        "BP 6 rue d'Alger Description.\n"
    )
    headers = _real_annexe_headers(text)
    spans = _merge_same_header_spans(headers, len(text))
    assert len(spans) == 1
    assert spans[0][2] == "Annexe X"


def test_merge_keeps_different_arrondissements_separate():
    """Every REG2A10 arrondissement's header resolves to the same roman
    numeral ("Annexe X") -- merging must key off the RAW header text (which
    differs, "DU 1ER" vs "DU 2E"), not the resolved label, or all
    arrondissements would collapse into one span and lose their boundary.
    """
    text = (
        "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 1ER ARRONDISSEMENT\n"
        "BP 3 rue d'Alger Description.\n"
        "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 2E ARRONDISSEMENT\n"
        "BP 6 rue d'Aboukir Description.\n"
    )
    headers = _real_annexe_headers(text)
    spans = _merge_same_header_spans(headers, len(text))
    assert len(spans) == 2
    assert [s[2] for s in spans] == ["Annexe X", "Annexe X"]
    assert spans[0][0] < spans[1][0]


# ---------------------------------------------------------------------------
# chunk_text_by_table: row preservation (the actual CH-06 fix)
# ---------------------------------------------------------------------------

# Reconstructed shape of REG2A1_MS1.pdf's Annexe V (13 real rows for "1er",
# per the ingestion audit) -- enough to exercise both single-line and
# multi-line (wrapped address) rows.
_ANNEXE_V_1ER = (
    "Annexe V : Liste des emplacements\n"
    "réservés en vue de la réalisation de\n"
    "certains types de logements\n"
    "En application des articles L.151-41, 4°, du code de l'urbanisme.\n"
    "Arrondissement Adresse Type de réserve\n"
    "1er\n"
    "4 rue d'Argenteuil\n"
    "11 rue de l'Echelle LS 100-60\n"
    "1er 26 rue du Bouloi LS 100-100\n"
    "1er 4 rue Jean Lantier LS 100-100\n"
)


def test_ls_rows_never_split_across_chunks():
    chunks = chunk_text_by_table(_ANNEXE_V_1ER, CHUNK_SIZE, source_path="REG2A1_MS1.pdf")
    contents = [c[1] for c in chunks]
    # The two single-line rows must each survive intact in exactly one chunk.
    assert any("1er 26 rue du Bouloi LS 100-100" in c for c in contents)
    assert any("1er 4 rue Jean Lantier LS 100-100" in c for c in contents)
    # The wrapped multi-address row must stay together, not be split between
    # its arrondissement line and its address/code lines.
    assert any(
        "4 rue d'Argenteuil" in c and "11 rue de l'Echelle LS 100-60" in c
        for c in contents
    )


def test_all_chunks_tagged_annexe_v():
    chunks = chunk_text_by_table(_ANNEXE_V_1ER, CHUNK_SIZE, source_path="REG2A1_MS1.pdf")
    assert chunks
    assert all(section == "Annexe V" for _, _, section, _ in chunks)
    assert all(content.startswith("[Section: Annexe V]\n") for _, content, _, _ in chunks)


def test_genuine_ls_rows_tagged_table_row():
    """Every chunk containing a real LS/BRS row is tagged table_row -- note
    the FIRST such chunk also bundles the section's intro paragraph (by
    design, see _table_rows_for_segment's LS/BRS branch: row boundaries are
    "previous row's end -> this row's end", and there is no previous row
    before the first one), so it's still correctly a table_row chunk, not
    pure prose.
    """
    chunks = chunk_text_by_table(_ANNEXE_V_1ER, CHUNK_SIZE, source_path="REG2A1_MS1.pdf")
    row_chunks = [c for c in chunks if "LS 100-100" in c[1] or "LS 100-60" in c[1]]
    assert row_chunks
    assert all(chunk_type == "table_row" for _, _, _, chunk_type in row_chunks)


# Reconstructed shape of a REG2A10 Annexe X page: a running header, then
# several BP entries, one with a long multi-paragraph "Motivation" that
# alone exceeds chunk_size (the real corpus has entries up to ~5.2k chars;
# see check.py's TABLE_ROW_MAX_LEN exception).
_ANNEXE_X_PAGE = (
    "ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 1ER ARRONDISSEMENT\n"
    "Type Localisation Motivation\n"
    "BP 3 rue d'Alger Immeuble de rapport construit en 1834.\n"
    "BP 6 à 8 rue de l'Amiral De Coligny " + ("Longue motivation historique détaillée. " * 40) + "\n"
    "BP 25 rue de l'Arbre Sec Maison présentant une façade en pierre de taille.\n"
)


def test_patrimoine_rows_never_split_even_when_oversized():
    chunks = chunk_text_by_table(_ANNEXE_X_PAGE, CHUNK_SIZE, source_path="REG2A10_1DE2_MS1.pdf")
    contents = [c[1] for c in chunks]
    assert any("BP 3 rue d'Alger" in c and "Immeuble de rapport construit en 1834." in c for c in contents)
    long_row = next(c for c in contents if "BP 6 à 8 rue de l'Amiral De Coligny" in c)
    assert "Longue motivation historique détaillée." in long_row
    assert len(long_row) > CHUNK_SIZE  # kept whole, not split -- within TABLE_ROW_MAX_LEN
    assert len(long_row) < TABLE_ROW_MAX_LEN
    assert any("BP 25 rue de l'Arbre Sec" in c for c in contents)


def test_patrimoine_rows_tagged_table_row():
    chunks = chunk_text_by_table(_ANNEXE_X_PAGE, CHUNK_SIZE, source_path="REG2A10_1DE2_MS1.pdf")
    bp_rows = [c for c in chunks if c[1].startswith("[Section: Annexe X]\nBP ")]
    assert len(bp_rows) == 3
    assert all(chunk_type == "table_row" for _, _, _, chunk_type in bp_rows)
    # The running-header preamble (before the first BP entry) is separate
    # prose here (unlike the LS/BRS branch), and correctly untagged.
    preamble = next(c for c in chunks if "Type Localisation Motivation" in c[1])
    assert preamble[3] is None


def test_oversized_preamble_still_bounded():
    """A stray incidental row-pattern match deep in an otherwise free-text
    span must not turn the preceding prose into one giant unbounded chunk
    -- only a genuine matched row is allowed to exceed chunk_size."""
    prose = "Du texte de remplissage sans structure de ligne particulière. " * 40
    text = "Annexe II : Liste des périmètres devant faire l'objet d'un projet\n" + prose
    chunks = chunk_text_by_table(text, CHUNK_SIZE, source_path="REG2A1_MS1.pdf")
    assert all(len(content) <= CHUNK_SIZE + 200 for _, content, _, _ in chunks)
    # Fallback char-split prose is never tagged table_row.
    assert all(chunk_type is None for _, _, _, chunk_type in chunks)


def test_no_headers_falls_back_to_fixed_size_split():
    text = "Du texte sans aucun en-tête d'annexe reconnaissable. " * 5
    chunks = chunk_text_by_table(text, CHUNK_SIZE, source_path="REG2A1_MS1.pdf")
    assert chunks
    assert all(section is None for _, _, section, _ in chunks)
    assert all(chunk_type is None for _, _, _, chunk_type in chunks)
