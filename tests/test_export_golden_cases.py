"""Unit tests for scripts/export_golden_cases.py — the fidelity-rules-driven
Notion "Golden Cases v2" -> eval/golden_dataset.json export. See the
module docstring there for the fidelity rules being tested (no invention, no
summarization, verbatim text, empty -> null/[], closed enum maps that fail
loudly on drift, consistency guard).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_golden_cases import (  # noqa: E402
    ConsistencyGuardError,
    MappingError,
    build_diff_report,
    check_consistency_guard,
    parse_row,
)


def _prop_title(text):
    return {"id": "t", "type": "title", "title": [{"type": "text", "text": {"content": text}, "plain_text": text}] if text else []}


def _prop_rich(text):
    if text is None:
        return {"id": "r", "type": "rich_text", "rich_text": []}
    return {"id": "r", "type": "rich_text", "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}]}


def _prop_select(name):
    return {"id": "s", "type": "select", "select": ({"name": name} if name else None)}


def _prop_multi(names):
    return {"id": "m", "type": "multi_select", "multi_select": [{"name": n} for n in names]}


def _prop_checkbox(value):
    return {"id": "c", "type": "checkbox", "checkbox": value}


def _page(**overrides):
    """A minimal, fully-populated valid page — tests override just what they need."""
    props = {
        "Cas": _prop_title("UC-99"),
        "Persona": _prop_select("Architecte"),
        "Question": _prop_rich("Une question ?"),
        "Contexte": _prop_rich("Un contexte."),
        "Type de cas": _prop_select("Couverture"),
        "Type d'échec testé": _prop_select("Retrieval [R]"),
        "Famille attendue": _prop_multi(["Règlement", "Annexe"]),
        "Articles / ressources attendus": _prop_rich("UG.3.2, UG.3.3 (à confirmer)"),
        "Answerable": _prop_select("Oui"),
        "Réponse attendue": _prop_rich("La réponse attendue."),
        "Ressource à pointer": _prop_rich("Une ressource."),
        "Critère de réussite": _prop_rich("Un critère."),
        "Reproche Charline (verbatim)": _prop_rich(None),
        "Fiabilité référence": _prop_select("Hypothèse à valider"),
        "Validé Charline": _prop_checkbox(False),
        "Priorité": _prop_select("⭐⭐⭐⭐"),
    }
    props.update(overrides)
    return {"id": "page-1", "properties": props}


# ---------------------------------------------------------------------------
# Verbatim copy / schema shape
# ---------------------------------------------------------------------------

def test_parse_row_produces_exact_schema_keys():
    row = parse_row(_page())
    assert set(row.keys()) == {
        "id", "persona", "question", "context", "type_cas", "type_echec",
        "famille_attendue", "articles_attendus", "answerable", "reponse_attendue",
        "ressource_a_pointer", "critere_reussite", "reproche_charline",
        "fiabilite", "validated", "priorite",
    }


def test_text_fields_copied_verbatim_not_summarized():
    long_text = "Phrase un. Phrase deux avec des détails précis. Phrase trois — conclusion nuancée."
    row = parse_row(_page(**{"Réponse attendue": _prop_rich(long_text)}))
    assert row["reponse_attendue"] == long_text


def test_empty_rich_text_becomes_null_not_invented():
    row = parse_row(_page(**{"Reproche Charline (verbatim)": _prop_rich(None)}))
    assert row["reproche_charline"] is None


def test_empty_select_becomes_null():
    row = parse_row(_page(**{"Persona": _prop_select(None)}))
    assert row["persona"] is None


def test_empty_multi_select_becomes_empty_list_not_null():
    row = parse_row(_page(**{"Famille attendue": _prop_multi([])}))
    assert row["famille_attendue"] == []


def test_checkbox_always_bool():
    row = parse_row(_page(**{"Validé Charline": _prop_checkbox(True)}))
    assert row["validated"] is True


def test_fiabilite_kept_verbatim_french_no_enum_mapping():
    row = parse_row(_page(**{"Fiabilité référence": _prop_select("Gold standard")}))
    assert row["fiabilite"] == "Gold standard"


def test_famille_attendue_kept_verbatim_no_slug_mapping():
    """No invented mapping to internal family slugs (reglement_ecrit, etc.) —
    the Notion labels are copied through as-is."""
    row = parse_row(_page(**{"Famille attendue": _prop_multi(["Règlement", "OAP"])}))
    assert row["famille_attendue"] == ["Règlement", "OAP"]


# ---------------------------------------------------------------------------
# articles_attendus comma-split parsing
# ---------------------------------------------------------------------------

def test_articles_split_on_comma_and_stripped():
    row = parse_row(_page(**{"Articles / ressources attendus": _prop_rich("UG.3.2, UG.3.3,  Annexe X")}))
    assert row["articles_attendus"] == ["UG.3.2", "UG.3.3", "Annexe X"]


def test_articles_parenthetical_annotation_stays_attached_to_its_entry():
    raw = "UG.3.1.2 (à confirmer), figure FNE (à confirmer)"
    row = parse_row(_page(**{"Articles / ressources attendus": _prop_rich(raw)}))
    assert row["articles_attendus"] == ["UG.3.1.2 (à confirmer)", "figure FNE (à confirmer)"]


def test_empty_articles_field_becomes_empty_list():
    row = parse_row(_page(**{"Articles / ressources attendus": _prop_rich(None)}))
    assert row["articles_attendus"] == []


# ---------------------------------------------------------------------------
# Closed enum maps — fail loudly on drift, never guess
# ---------------------------------------------------------------------------

def test_type_cas_maps_known_values():
    assert parse_row(_page(**{"Type de cas": _prop_select("Couverture")}))["type_cas"] == "couverture"
    assert parse_row(_page(**{"Type de cas": _prop_select("Régression")}))["type_cas"] == "regression"


def test_type_echec_maps_known_values():
    assert parse_row(_page(**{"Type d'échec testé": _prop_select("Retrieval [R]")}))["type_echec"] == "R"
    assert parse_row(_page(**{"Type d'échec testé": _prop_select("Synthèse [S]")}))["type_echec"] == "S"
    assert parse_row(_page(**{"Type d'échec testé": _prop_select("Mixte [R+S]")}))["type_echec"] == "mixte"


def test_answerable_maps_known_values():
    assert parse_row(_page(**{"Answerable": _prop_select("Oui")}))["answerable"] == "oui"
    assert parse_row(_page(**{"Answerable": _prop_select("Renvoi doc")}))["answerable"] == "renvoi"
    assert parse_row(_page(**{"Answerable": _prop_select("Non")}))["answerable"] == "non"


def test_unrecognized_type_cas_raises_instead_of_guessing():
    with pytest.raises(MappingError):
        parse_row(_page(**{"Type de cas": _prop_select("Un nouveau statut jamais vu")}))


def test_unrecognized_answerable_raises_instead_of_guessing():
    with pytest.raises(MappingError):
        parse_row(_page(**{"Answerable": _prop_select("Peut-être")}))


def test_empty_enum_select_is_null_not_an_error():
    """Empty is a legitimate (if incomplete) state -- distinct from an
    unrecognized non-empty value, which is an error."""
    row = parse_row(_page(**{"Type de cas": _prop_select(None)}))
    assert row["type_cas"] is None


def test_empty_case_id_raises():
    with pytest.raises(MappingError):
        parse_row(_page(**{"Cas": _prop_title(None)}))


def test_missing_property_in_schema_raises_not_silently_skips():
    page = _page()
    del page["properties"]["Persona"]
    with pytest.raises(MappingError):
        parse_row(page)


# ---------------------------------------------------------------------------
# Consistency guard
# ---------------------------------------------------------------------------

def test_guard_passes_when_no_case_is_both_fabricated_and_validated():
    cases = [
        {"id": "A", "fiabilite": "Fabriqué - à refaire", "validated": False},
        {"id": "B", "fiabilite": "Validé Charline", "validated": True},
    ]
    check_consistency_guard(cases)  # must not raise


def test_guard_raises_when_a_case_is_both_fabricated_and_validated():
    cases = [
        {"id": "UC-04", "fiabilite": "Fabriqué - à refaire", "validated": True},
    ]
    with pytest.raises(ConsistencyGuardError):
        check_consistency_guard(cases)


def test_guard_error_names_the_offending_case():
    cases = [{"id": "UC-04", "fiabilite": "Fabriqué - à refaire", "validated": True}]
    with pytest.raises(ConsistencyGuardError, match="UC-04"):
        check_consistency_guard(cases)


# ---------------------------------------------------------------------------
# Diff report
# ---------------------------------------------------------------------------

def test_diff_report_flags_added_and_dropped_ids():
    legacy = [{"id": "UC-01", "question": "Q1", "expected_articles": ["UG.3.2"]}]
    new = [
        {"id": "UC-01", "question": "Q1", "articles_attendus": ["UG.3.2"]},
        {"id": "CH-02", "question": "Q2", "articles_attendus": ["UG.2.2"]},
    ]
    report = build_diff_report(legacy, new)
    assert "CH-02" in report
    assert "Added" in report or "added" in report.lower()


def test_diff_report_flags_changed_articles():
    legacy = [{"id": "UC-03", "question": "Q3", "expected_articles": ["UG.3.1.1"]}]
    new = [{"id": "UC-03", "question": "Q3", "articles_attendus": ["UG.3.1.2 (à confirmer)"]}]
    report = build_diff_report(legacy, new)
    assert "UC-03" in report
    assert "UG.3.1.1" in report
    assert "UG.3.1.2 (à confirmer)" in report


def test_diff_report_silent_on_unchanged_case():
    legacy = [{"id": "UC-01", "question": "Q1", "expected_articles": ["UG.3.2"]}]
    new = [{"id": "UC-01", "question": "Q1", "articles_attendus": ["UG.3.2"]}]
    report = build_diff_report(legacy, new)
    assert "none of the common cases" in report.lower()
