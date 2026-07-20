"""Regression tests for the annexe route (src/aria_rag/retriever.py) — T6's
fix for table_row chunks polluting ordinary retrieval (T5 audit) and for
CH-06's ranking/breadth gap (individual rows never surviving top-k
together). See SESSION_STATE.md's T6 entry for the full audit trail.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.indexer import Chunk
from aria_rag.retriever import _annexe_route, _detect_annexe_route, _is_table_row

# ---------------------------------------------------------------------------
# _detect_annexe_route — keyword conjunction, never a partial match
# ---------------------------------------------------------------------------


def test_ch06_wording_routes_to_annexe_v():
    q = "Peux-tu me donner la liste des adresses du 1er arrondissement (emplacements réservés logement) ?"
    assert _detect_annexe_route(q) == "Annexe V"


def test_ch05_wording_routes_to_annexe_i():
    q = "Donne la liste des secteurs soumis à des dispositions particulières"
    assert _detect_annexe_route(q) == "Annexe I"


def test_batiment_protege_routes_to_annexe_x():
    q = "Je souhaite modifier la façade d'un bâtiment protégé, que dois-je respecter ?"
    assert _detect_annexe_route(q) == "Annexe X"


def test_emplacements_reserves_equipements_does_not_route():
    """The OTHER 'emplacements réservés' mechanism (public facilities, not
    logement) must not be conflated with Annexe V — see T4's disambiguation."""
    q = "Quelles sont les règles pour un emplacement réservé pour équipement public ?"
    assert _detect_annexe_route(q) is None


def test_ch02_volets_does_not_route_to_annexe_x():
    """A bare 'volets' question (CH-02, expects UG.2.2 prose) must not be
    hijacked by the Annexe X route just because it's about aspect extérieur."""
    q = "Je souhaite installer des volets, quelles sont les règles à respecter dans le 10e arrondissement ?"
    assert _detect_annexe_route(q) is None


def test_ch03_toiture_does_not_route_without_explicit_protection_wording():
    """CH-03 (toit zinc -> tuile) doesn't say the building is protected --
    routing to Annexe X unconditionally would be forcing an assumption the
    query doesn't support. See SESSION_STATE.md for the explicit call not
    to force this."""
    q = "Je souhaite remplacer mon toit en zinc par un toit en tuile. Est-ce que c'est possible ?"
    assert _detect_annexe_route(q) is None


def test_generic_ug_question_never_routes():
    q = "Quelle est la hauteur maximale constructible sur une parcelle en zone UG à Paris 11e ?"
    assert _detect_annexe_route(q) is None


# ---------------------------------------------------------------------------
# _annexe_route — section-filtered lookup, never ranking
# ---------------------------------------------------------------------------


def _row(chunk_id, section, content, chunk_type="table_row", source_path="REG2A1_MS1.pdf"):
    return Chunk(
        chunk_id=chunk_id, source_path=source_path, doc_family="reglement_ecrit",
        content=content, section=section, chunk_type=chunk_type,
    )


def test_route_returns_none_when_no_keywords_match():
    chunks = [_row("REG2A1_MS1-1", "Annexe V", "1er 15 rue d'Argenteuil LS 100-100")]
    assert _annexe_route("Quelle est la hauteur maximale en zone UG ?", chunks, limit=10) is None


def test_route_returns_none_when_annexe_has_no_matching_chunks():
    """A route fires but the corpus has zero chunks for that annexe (e.g. a
    future corpus change) -- must fall through, never return an empty list
    that a caller could mistake for "no answer exists"."""
    chunks = [_row("REG2A1_MS1-1", "Annexe I", "1er Les Halles non soumis -")]
    q = "Peux-tu me donner la liste des adresses du 1er arrondissement (emplacements réservés logement) ?"
    assert _annexe_route(q, chunks, limit=10) is None


def test_route_ignores_prose_chunks_even_in_the_right_section():
    """Only chunk_type=table_row chunks are eligible -- a prose/preamble
    chunk that happens to carry the same section value is not a row."""
    chunks = [
        _row("REG2A1_MS1-0", "Annexe V", "Annexe V : Liste des emplacements réservés...", chunk_type=None),
        _row("REG2A1_MS1-1", "Annexe V", "1er 15 rue d'Argenteuil LS 100-100"),
    ]
    q = "emplacements réservés logement liste complète"
    hits = _annexe_route(q, chunks, limit=10)
    assert hits is not None
    assert len(hits) == 1
    assert "15 rue d'Argenteuil" in hits[0].content


def test_route_is_exhaustive_for_arrondissement_bypassing_limit():
    """CH-06's actual requirement: every '1er' row returned, even though
    limit is far smaller -- this is the whole point of the route."""
    chunks = [_row(f"REG2A1_MS1-{i}", "Annexe V", f"1er {i} rue Test LS 100-100") for i in range(17)]
    chunks += [_row("REG2A1_MS1-99", "Annexe V", "2e 1 rue Autre LS 100-100")]
    q = "Peux-tu me donner la liste des adresses du 1er arrondissement (emplacements réservés logement) ?"
    hits = _annexe_route(q, chunks, limit=5)
    assert len(hits) == 17
    assert all(h.content.startswith("1er ") for h in hits)


def test_route_without_arrondissement_respects_limit():
    chunks = [_row(f"REG2A1_MS1-{i}", "Annexe V", f"{i%20+1}e {i} rue Test LS 100-100") for i in range(50)]
    q = "liste des emplacements réservés logement"
    hits = _annexe_route(q, chunks, limit=5)
    assert len(hits) == 5


def test_route_orders_by_document_position_not_score():
    chunks = [
        _row("REG2A1_MS1-9", "Annexe V", "1er 9 rue Test LS 100-100"),
        _row("REG2A1_MS1-2", "Annexe V", "1er 2 rue Test LS 100-100"),
        _row("REG2A1_MS1-15", "Annexe V", "1er 15 rue Test LS 100-100"),
    ]
    q = "emplacements réservés logement 1er arrondissement liste"
    hits = _annexe_route(q, chunks, limit=10)
    assert [h.content for h in hits] == [
        "1er 2 rue Test LS 100-100",
        "1er 9 rue Test LS 100-100",
        "1er 15 rue Test LS 100-100",
    ]


# ---------------------------------------------------------------------------
# _is_table_row
# ---------------------------------------------------------------------------


def test_is_table_row_true_only_for_table_row_type():
    assert _is_table_row(_row("X-0", "Annexe V", "content", chunk_type="table_row"))
    assert not _is_table_row(_row("X-1", "UG.3.1", "content", chunk_type=None))
