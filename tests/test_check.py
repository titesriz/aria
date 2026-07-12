"""Regression tests for the ingestion invariants suite (src/aria_rag/check.py).

Each test constructs a synthetic violation and asserts the corresponding
invariant trips (status="fail" or "warn" per spec) — the whole point of
this suite is that these can no longer slip through as "ad hoc inspection
missed it".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.check import (
    check_coverage,
    check_dedup_ledger,
    check_encoding,
    check_family_coverage,
    check_fragment_floor,
    check_manifest_consistency,
    check_metadata_integrity,
    check_referentiel_coverage,
    check_size_cap,
    run_checks,
)
from aria_rag.config import Settings
from aria_rag.corpus_mapping import MappingRule
from aria_rag.indexer import Chunk, IndexedFile
from aria_rag.referentiel import Piece


def _chunk(**overrides) -> Chunk:
    defaults = dict(
        chunk_id="X-0",
        source_path="/docs/X.pdf",
        doc_family="reglement_ecrit",
        content="a" * 200,
        page=1,
        page_end=1,
        section="UG.1.1",
    )
    defaults.update(overrides)
    return Chunk(**defaults)


def _settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(docs_dir=tmp_path / "docs", index_dir=tmp_path / "index")
    defaults.update(overrides)
    s = Settings(**defaults)
    s.index_dir.mkdir(parents=True, exist_ok=True)
    return s


# ---------------------------------------------------------------------------
# 1. Family coverage
# ---------------------------------------------------------------------------

def test_family_coverage_passes_for_documented_unserved_family(tmp_path):
    settings = _settings(tmp_path, family_slots={"reglement_ecrit": 6})
    chunks = [_chunk(doc_family="cch")]  # documented in KNOWN_UNSERVED_FAMILIES since Stage A
    result = check_family_coverage(chunks, settings, tmp_path / "reports")
    assert result.status == "pass"
    assert result.count == 0


def test_family_coverage_fails_for_other_since_stage_a(tmp_path):
    """Stage A's corpus_mapping.yaml classifies every file under
    Ressources/ — a chunk landing in "other" now means something is
    genuinely unmapped, not a known, accepted catch-all (that was CCH,
    before it got its own family). "other" was deliberately removed from
    KNOWN_UNSERVED_FAMILIES; this is the regression guard for that.
    """
    settings = _settings(tmp_path, family_slots={"reglement_ecrit": 6})
    chunks = [_chunk(doc_family="other", chunk_id="X-0")]
    result = check_family_coverage(chunks, settings, tmp_path / "reports")
    assert result.status == "fail"
    assert result.count == 1


def test_family_coverage_fails_for_new_undocumented_family(tmp_path):
    """A brand-new family absent from both slot config and the documented
    allowlist — the CCH-in-'other' incident this invariant guards against,
    generalized to catch a NEW unmapped family too.
    """
    settings = _settings(tmp_path, family_slots={"reglement_ecrit": 6})
    chunks = [_chunk(doc_family="some_future_family", chunk_id="FUT-0")]
    result = check_family_coverage(chunks, settings, tmp_path / "reports")
    assert result.status == "fail"
    assert result.count == 1


# ---------------------------------------------------------------------------
# 2. Size cap
# ---------------------------------------------------------------------------

def test_size_cap_passes_within_tolerance(tmp_path):
    settings = _settings(tmp_path, chunk_size=1200)
    chunks = [_chunk(content="a" * (1200 + 112))]  # exactly at the documented cap
    result = check_size_cap(chunks, settings, tmp_path / "reports")
    assert result.status == "pass"


def test_size_cap_fails_beyond_tolerance(tmp_path):
    settings = _settings(tmp_path, chunk_size=1200)
    chunks = [_chunk(content="a" * (1200 + 113))]  # one over the documented cap
    result = check_size_cap(chunks, settings, tmp_path / "reports")
    assert result.status == "fail"
    assert result.count == 1


# ---------------------------------------------------------------------------
# 3. Metadata integrity
# ---------------------------------------------------------------------------

def test_metadata_integrity_passes_for_title_page_exception(tmp_path):
    """chunk_id ending in -0 is the documented title-page exception — a
    reglement_ecrit chunk before any structural header legitimately has no
    section.
    """
    chunks = [_chunk(chunk_id="REG9-0", section=None)]
    result = check_metadata_integrity(chunks, tmp_path / "reports")
    assert result.status == "pass"


def test_metadata_integrity_fails_for_unexplained_null_section(tmp_path):
    chunks = [_chunk(chunk_id="REG9-5", section=None)]  # not the first chunk
    result = check_metadata_integrity(chunks, tmp_path / "reports")
    assert result.status == "fail"
    assert result.count == 1


def test_metadata_integrity_fails_for_page_end_before_page(tmp_path):
    chunks = [_chunk(page=5, page_end=3)]
    result = check_metadata_integrity(chunks, tmp_path / "reports")
    assert result.status == "fail"


def test_metadata_integrity_fails_for_null_page(tmp_path):
    chunks = [_chunk(page=None)]
    result = check_metadata_integrity(chunks, tmp_path / "reports")
    assert result.status == "fail"


def test_metadata_integrity_fails_for_annexe_pollution_regression(tmp_path):
    """Lowercase-leading 'annexe' as a section value — the exact signature
    of the bug fixed by _ANNEXE_HEADER's case-sensitivity requirement
    (indexer.py). Must never reappear.
    """
    chunks = [_chunk(section="annexe I du tome 2 du règlement écrit indique")]
    result = check_metadata_integrity(chunks, tmp_path / "reports")
    assert result.status == "fail"
    assert result.count == 1


def test_metadata_integrity_passes_for_genuine_uppercase_annexe_section(tmp_path):
    chunks = [_chunk(section="ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES")]
    result = check_metadata_integrity(chunks, tmp_path / "reports")
    assert result.status == "pass"


# ---------------------------------------------------------------------------
# 4. Encoding
# ---------------------------------------------------------------------------

def test_encoding_fails_on_nul_byte(tmp_path):
    chunks = [_chunk(content="hello" + chr(0x0000) + "world")]
    result = check_encoding(chunks, tmp_path / "reports")
    assert result.status == "fail"
    assert result.count == 1


def test_encoding_fails_on_replacement_char(tmp_path):
    chunks = [_chunk(content="hello" + chr(0xFFFD) + "world")]
    result = check_encoding(chunks, tmp_path / "reports")
    assert result.status == "fail"


def test_encoding_warns_only_on_u008c(tmp_path):
    chunks = [_chunk(content="hello" + chr(0x008C) + "world")]
    result = check_encoding(chunks, tmp_path / "reports")
    assert result.status == "warn"
    assert result.count == 0  # count tracks hard-fail chars only


def test_encoding_passes_clean_content(tmp_path):
    chunks = [_chunk(content="hello world")]
    result = check_encoding(chunks, tmp_path / "reports")
    assert result.status == "pass"


def _known_encoding_path(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "known_encoding_failures.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


def test_encoding_passes_for_documented_replacement_char(tmp_path):
    """RP_DIAGNOSTIC.pdf's U+FFFD hits (font-subset corruption of periods,
    confirmed by the extraction audit) are documented debt, not a live
    regression -- the allowlist mechanics mirror
    checks/known_manifest_desync.json.
    """
    settings = _settings(tmp_path)
    source_path = str(settings.docs_dir / "RP_DIAGNOSTIC.pdf")
    known_path = _known_encoding_path(tmp_path, [
        {"source_path": "RP_DIAGNOSTIC.pdf", "char": "U+FFFD"},
    ])
    chunks = [_chunk(source_path=source_path, content="hello" + chr(0xFFFD) + "world", doc_family="rapport_presentation")]
    result = check_encoding(chunks, tmp_path / "reports", settings, known_path)
    assert result.status == "pass"
    assert result.count == 0
    assert "known-failure (documented" in result.message


def test_encoding_fails_for_replacement_char_in_other_file(tmp_path):
    """The allowlist is file-scoped -- U+FFFD in any file other than the
    documented RP_DIAGNOSTIC.pdf entry must still fail loudly.
    """
    settings = _settings(tmp_path)
    source_path = str(settings.docs_dir / "OTHER_FILE.pdf")
    known_path = _known_encoding_path(tmp_path, [
        {"source_path": "RP_DIAGNOSTIC.pdf", "char": "U+FFFD"},
    ])
    chunks = [_chunk(source_path=source_path, content="hello" + chr(0xFFFD) + "world")]
    result = check_encoding(chunks, tmp_path / "reports", settings, known_path)
    assert result.status == "fail"
    assert result.count == 1


def test_encoding_fails_for_nul_byte_even_if_allowlisted(tmp_path):
    """U+0000/U+0002 were fixed corpus-wide -- a reappearance is always a
    regression, so no allowlist entry (even a mistaken one) can suppress it.
    """
    settings = _settings(tmp_path)
    source_path = str(settings.docs_dir / "RP_DIAGNOSTIC.pdf")
    known_path = _known_encoding_path(tmp_path, [
        {"source_path": "RP_DIAGNOSTIC.pdf", "char": "U+0000"},
    ])
    chunks = [_chunk(source_path=source_path, content="hello" + chr(0x0000) + "world")]
    result = check_encoding(chunks, tmp_path / "reports", settings, known_path)
    assert result.status == "fail"
    assert result.count == 1


# ---------------------------------------------------------------------------
# 5. Manifest consistency
# ---------------------------------------------------------------------------

def test_manifest_consistency_fails_for_new_undocumented_desync(tmp_path):
    settings = _settings(tmp_path)
    chunks = [_chunk(source_path=str(tmp_path / "docs" / "A.pdf"))]
    manifest = [IndexedFile(source_path=str(tmp_path / "docs" / "A.pdf"), size_bytes=1, modified_time=1.0, chunk_count=5)]
    empty_known = tmp_path / "known_desync.json"
    empty_known.write_text(json.dumps({"entries": []}), encoding="utf-8")
    result = check_manifest_consistency(chunks, manifest, settings, tmp_path / "reports", known_desync_path=empty_known)
    assert result.status == "fail"
    assert result.count == 1


def test_manifest_consistency_passes_for_documented_desync(tmp_path):
    settings = _settings(tmp_path)
    docs_dir = settings.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    source_path = str(docs_dir / "A.pdf")
    chunks = [_chunk(source_path=source_path)]
    manifest = [IndexedFile(source_path=source_path, size_bytes=1, modified_time=1.0, chunk_count=5)]
    known = tmp_path / "known_desync.json"
    known.write_text(json.dumps({"entries": [{"source_path": "A.pdf", "manifest_chunk_count": 5, "actual_chunk_count": 1}]}), encoding="utf-8")
    result = check_manifest_consistency(chunks, manifest, settings, tmp_path / "reports", known_desync_path=known)
    assert result.status == "pass"
    assert result.count == 0


# ---------------------------------------------------------------------------
# 6. Coverage
# ---------------------------------------------------------------------------

def test_coverage_fails_for_pdf_missing_from_manifest(tmp_path):
    settings = _settings(tmp_path)
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    (settings.docs_dir / "orphan.pdf").write_bytes(b"%PDF-1.4")
    empty_known = tmp_path / "known_zero.json"
    empty_known.write_text(json.dumps({"entries": []}), encoding="utf-8")
    result = check_coverage([], settings, tmp_path / "reports", known_zero_chunk_path=empty_known)
    assert result.status == "fail"
    assert result.count == 1


def test_coverage_fails_for_new_unexplained_zero_chunk_file(tmp_path):
    settings = _settings(tmp_path)
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    source_path = str(settings.docs_dir / "blank.pdf")
    (settings.docs_dir / "blank.pdf").write_bytes(b"%PDF-1.4")
    manifest = [IndexedFile(source_path=source_path, size_bytes=1, modified_time=1.0, chunk_count=0)]
    empty_known = tmp_path / "known_zero.json"
    empty_known.write_text(json.dumps({"entries": []}), encoding="utf-8")
    result = check_coverage(manifest, settings, tmp_path / "reports", known_zero_chunk_path=empty_known)
    assert result.status == "fail"
    assert result.count == 1


def test_coverage_passes_for_documented_zero_chunk_file(tmp_path):
    settings = _settings(tmp_path)
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    (settings.docs_dir / "legend.pdf").write_bytes(b"%PDF-1.4")
    source_path = str(settings.docs_dir / "legend.pdf")
    manifest = [IndexedFile(source_path=source_path, size_bytes=1, modified_time=1.0, chunk_count=0)]
    known = tmp_path / "known_zero.json"
    known.write_text(json.dumps({"entries": ["legend.pdf"]}), encoding="utf-8")
    result = check_coverage(manifest, settings, tmp_path / "reports", known_zero_chunk_path=known)
    assert result.status == "pass"
    assert result.count == 0


def test_coverage_passes_for_superseded_file_not_in_manifest(tmp_path):
    """A validity=superseded file (corpus_mapping.yaml) is discovered on
    disk but deliberately never indexed — it must not be flagged as
    missing, unlike a genuinely forgotten file.
    """
    settings = _settings(tmp_path)
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    (settings.docs_dir / "REG1.pdf").write_bytes(b"%PDF-1.4")
    empty_known = tmp_path / "known_zero.json"
    empty_known.write_text(json.dumps({"entries": []}), encoding="utf-8")
    rules = [MappingRule(prefix="REG1.pdf", family="reglement_ecrit", norm_level="local", city="paris", validity="superseded")]
    result = check_coverage(
        [], settings, tmp_path / "reports", known_zero_chunk_path=empty_known, mapping_rules=rules
    )
    assert result.status == "pass"
    assert result.count == 0


def test_coverage_still_fails_for_missing_file_not_covered_by_a_superseded_rule(tmp_path):
    """A superseded rule for one file must not blanket-excuse a genuinely
    different missing file.
    """
    settings = _settings(tmp_path)
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    (settings.docs_dir / "REG1.pdf").write_bytes(b"%PDF-1.4")
    (settings.docs_dir / "orphan.pdf").write_bytes(b"%PDF-1.4")
    empty_known = tmp_path / "known_zero.json"
    empty_known.write_text(json.dumps({"entries": []}), encoding="utf-8")
    rules = [MappingRule(prefix="REG1.pdf", family="reglement_ecrit", norm_level="local", city="paris", validity="superseded")]
    result = check_coverage(
        [], settings, tmp_path / "reports", known_zero_chunk_path=empty_known, mapping_rules=rules
    )
    assert result.status == "fail"
    assert result.count == 1


# ---------------------------------------------------------------------------
# 7. Fragment floor
# ---------------------------------------------------------------------------

def test_fragment_floor_warns_never_fails(tmp_path):
    chunks = [_chunk(content="short")]
    result = check_fragment_floor(chunks, tmp_path / "reports")
    assert result.status == "warn"
    assert result.count == 1


def test_fragment_floor_passes_above_threshold(tmp_path):
    chunks = [_chunk(content="a" * 30)]
    result = check_fragment_floor(chunks, tmp_path / "reports")
    assert result.status == "pass"


# ---------------------------------------------------------------------------
# 8. Dedup ledger
# ---------------------------------------------------------------------------

def test_dedup_ledger_warns_when_not_yet_generated(tmp_path):
    settings = _settings(tmp_path)
    result = check_dedup_ledger(settings, tmp_path / "reports")
    assert result.status == "warn"


def test_dedup_ledger_fails_on_malformed_file(tmp_path):
    settings = _settings(tmp_path)
    (settings.index_dir / "dedup_ledger.json").write_text("not json", encoding="utf-8")
    result = check_dedup_ledger(settings, tmp_path / "reports")
    assert result.status == "fail"


def test_dedup_ledger_passes_for_valid_ledger(tmp_path):
    settings = _settings(tmp_path)
    ledger = [{
        "removed_chunk_id": "A-1", "removed_source_path": "/docs/A.pdf",
        "kept_chunk_id": "B-1", "kept_source_path": "/docs/B.pdf",
        "content_hash": "abc123",
    }]
    (settings.index_dir / "dedup_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    result = check_dedup_ledger(settings, tmp_path / "reports")
    assert result.status == "pass"
    assert result.count == 1


# ---------------------------------------------------------------------------
# 9. Referentiel coverage
# ---------------------------------------------------------------------------

def _piece(**overrides) -> Piece:
    defaults = dict(
        id="p1", official_name="Pièce 1", status_opposabilite="opposable",
        family="annexes", norm_level="local", expected=True, file_match="Annexes",
        notes="",
    )
    defaults.update(overrides)
    return Piece(**defaults)


def test_referentiel_coverage_passes_when_every_file_mapped_and_no_gaps(tmp_path):
    settings = _settings(tmp_path)
    docs_dir = settings.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    source_path = str(docs_dir / "Annexes" / "A.pdf")
    manifest = [IndexedFile(source_path=source_path, size_bytes=1, modified_time=1.0, chunk_count=5)]
    pieces = [_piece(id="p1", file_match="Annexes", expected=True)]
    result = check_referentiel_coverage(manifest, pieces, settings, tmp_path / "reports")
    assert result.status == "pass"
    assert result.count == 0


def test_referentiel_coverage_fails_for_unmapped_indexed_file(tmp_path):
    settings = _settings(tmp_path)
    docs_dir = settings.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    source_path = str(docs_dir / "Unmapped" / "A.pdf")
    manifest = [IndexedFile(source_path=source_path, size_bytes=1, modified_time=1.0, chunk_count=5)]
    pieces = [_piece(id="p1", file_match="Annexes", expected=True)]
    result = check_referentiel_coverage(manifest, pieces, settings, tmp_path / "reports")
    assert result.status == "fail"
    assert result.count == 1


def test_referentiel_coverage_warns_for_expected_piece_with_no_file(tmp_path):
    settings = _settings(tmp_path)
    docs_dir = settings.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    manifest = []  # nothing indexed at all
    pieces = [_piece(id="annexe-sanitaire", file_match=None, expected=True)]
    result = check_referentiel_coverage(manifest, pieces, settings, tmp_path / "reports")
    assert result.status == "warn"
    assert result.count == 0  # count tracks unmapped files, not coverage gaps


def test_referentiel_coverage_does_not_warn_for_non_expected_absent_piece(tmp_path):
    settings = _settings(tmp_path)
    docs_dir = settings.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    pieces = [_piece(id="optional-piece", file_match=None, expected=False)]
    result = check_referentiel_coverage(manifest, pieces, settings, tmp_path / "reports")
    assert result.status == "pass"


# ---------------------------------------------------------------------------
# run_checks — strict mode
# ---------------------------------------------------------------------------

def test_run_checks_strict_raises_on_any_failure(tmp_path):
    settings = _settings(tmp_path, family_slots={"reglement_ecrit": 6})
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    chunks_json = settings.index_dir / "chunks.json"
    chunks_json.write_text(json.dumps([{
        "chunk_id": "X-0", "source_path": str(settings.docs_dir / "X.pdf"), "doc_family": "reglement_ecrit",
        "content": "a" * (1200 + 200), "page": 1, "page_end": 1, "section": "UG.1.1",
    }]), encoding="utf-8")
    (settings.index_dir / "manifest.json").write_text("[]", encoding="utf-8")
    try:
        run_checks(settings, strict=True)
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert exc.code != 0 and exc.code is not None


def test_run_checks_non_strict_does_not_raise(tmp_path):
    settings = _settings(tmp_path, family_slots={"reglement_ecrit": 6})
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    chunks_json = settings.index_dir / "chunks.json"
    chunks_json.write_text(json.dumps([{
        "chunk_id": "X-0", "source_path": str(settings.docs_dir / "X.pdf"), "doc_family": "reglement_ecrit",
        "content": "a" * (1200 + 200), "page": 1, "page_end": 1, "section": "UG.1.1",
    }]), encoding="utf-8")
    (settings.index_dir / "manifest.json").write_text("[]", encoding="utf-8")
    results = run_checks(settings, strict=False)  # must not raise despite the size-cap failure
    assert any(r.status == "fail" for r in results)
