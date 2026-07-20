"""Regression tests for config-driven corpus classification
(src/aria_rag/corpus_mapping.py) — Stage A of the CCH dual-source
prototype, replacing the hardcoded DOC_FAMILIES dict.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.corpus_mapping import (
    MappingRule,
    classify_path,
    load_rules,
    superseded_paths,
)

_RULES = [
    MappingRule(
        prefix="PLU/75 Paris/PLU Bioclimatique/Règlement/Pièces écrites/Tome 1/REG1.pdf",
        family="reglement_ecrit", norm_level="local", city="paris", validity="superseded",
    ),
    MappingRule(
        prefix="PLU/75 Paris/PLU Bioclimatique/Règlement/Pièces écrites",
        family="reglement_ecrit", norm_level="local", city="paris", validity="current",
    ),
    MappingRule(
        prefix="LEGIFRANCE/CCH",
        family="cch", norm_level="national", city=None, validity="current",
    ),
]


def _docs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Ressources"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# classify_path — longest-prefix match
# ---------------------------------------------------------------------------

def test_classify_path_matches_folder_rule(tmp_path):
    docs_dir = _docs_dir(tmp_path)
    path = docs_dir / "PLU/75 Paris/PLU Bioclimatique/Règlement/Pièces écrites/Tome 1/REG1_MS1.pdf"
    c = classify_path(path, docs_dir, _RULES)
    assert c.family == "reglement_ecrit"
    assert c.norm_level == "local"
    assert c.city == "paris"
    assert c.validity == "current"


def test_classify_path_longest_prefix_wins_over_folder_rule(tmp_path):
    """REG1.pdf has both a file-specific rule (superseded) and would match
    the broader folder rule (current) — the longer, more specific prefix
    must win.
    """
    docs_dir = _docs_dir(tmp_path)
    path = docs_dir / "PLU/75 Paris/PLU Bioclimatique/Règlement/Pièces écrites/Tome 1/REG1.pdf"
    c = classify_path(path, docs_dir, _RULES)
    assert c.validity == "superseded"
    assert c.family == "reglement_ecrit"  # unchanged from the folder rule — only validity differs


def test_classify_path_national_norm_level(tmp_path):
    docs_dir = _docs_dir(tmp_path)
    path = docs_dir / "LEGIFRANCE/CCH/LEGITEXT000006074096.pdf"
    c = classify_path(path, docs_dir, _RULES)
    assert c.family == "cch"
    assert c.norm_level == "national"
    assert c.city is None
    assert c.validity == "current"


def test_classify_path_no_match_is_other():
    docs_dir = Path("/docs")
    path = Path("/docs/SomeUnmappedFolder/file.pdf")
    c = classify_path(path, docs_dir, _RULES)
    assert c.family == "other"
    assert c.norm_level is None
    assert c.city is None
    assert c.validity == "current"  # unmapped is indexed, never silently dropped


def test_classify_path_nfc_normalizes_accents(tmp_path):
    """Same defensive posture as the old infer_doc_family(): NFD-decomposed
    accents (e.g. macOS HFS+ filesystem paths) must still match.
    """
    import unicodedata
    docs_dir = _docs_dir(tmp_path)
    nfd_path = docs_dir / unicodedata.normalize(
        "NFD", "PLU/75 Paris/PLU Bioclimatique/Règlement/Pièces écrites/Tome 1/REG1_MS1.pdf"
    )
    c = classify_path(nfd_path, docs_dir, _RULES)
    assert c.family == "reglement_ecrit"


# ---------------------------------------------------------------------------
# load_rules — real corpus_mapping.yaml
# ---------------------------------------------------------------------------

def test_load_rules_reads_real_corpus_mapping_yaml():
    path = Path(__file__).resolve().parents[1] / "corpus_mapping.yaml"
    rules = load_rules(path)
    families = {r.family for r in rules}
    assert "cch" in families
    assert "reglement_ecrit" in families
    superseded = [r for r in rules if r.validity == "superseded"]
    assert len(superseded) == 3
    assert all(r.prefix.endswith(".pdf") for r in superseded)


def test_load_rules_excludes_ann2a_map_plate():
    """ANN2A_2025_12_19.pdf's extracted text is nothing but its own repeated
    title banner (confirmed by direct chunk inspection) -- a map/plate with
    no substantive indexable content, excluded to stop it polluting
    retrieval with header-noise chunks.
    """
    path = Path(__file__).resolve().parents[1] / "corpus_mapping.yaml"
    rules = load_rules(path)
    excluded = [r for r in rules if r.validity == "excluded"]
    assert len(excluded) == 1
    assert excluded[0].prefix.endswith("ANN2A_2025_12_19.pdf")
    assert excluded[0].family == "annexes"


def test_load_rules_defaults_validity_to_current():
    rules = load_rules(Path(__file__).resolve().parents[1] / "corpus_mapping.yaml")
    cch_rule = next(r for r in rules if r.family == "cch")
    assert cch_rule.validity == "current"


# ---------------------------------------------------------------------------
# superseded_paths
# ---------------------------------------------------------------------------

def test_superseded_paths_returns_all_three_reg_files():
    rules = load_rules(Path(__file__).resolve().parents[1] / "corpus_mapping.yaml")
    docs_dir = Path("/docs")  # unused by superseded_paths, rules already store final prefixes
    paths = superseded_paths(docs_dir, rules)
    assert len(paths) == 3
    assert any(p.endswith("REG1.pdf") for p in paths)
    assert any(p.endswith("REG2A1.pdf") for p in paths)
    assert any(p.endswith("REG2A10_1DE2.pdf") for p in paths)
