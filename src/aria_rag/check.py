"""Ingestion invariant checks.

Catches corpus-integrity regressions that were previously only found by ad
hoc inspection: a family with zero retrieval slots, chunks over the size
cap, polluted section metadata, stray control characters, a manifest/
chunks.json desync, a PDF silently missing coverage. Runs automatically
after `aria-rag ingest` and standalone via `aria-rag check` (--strict exits
non-zero on any FAIL, for CI).

Each invariant is checked against the CURRENT corpus and, where a known,
pre-existing exception list applies (manifest desync, zero-chunk files),
against a documented allowlist under checks/ — so today's known debt stays
green while a genuinely NEW instance of the same problem fails loudly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aria_rag.config import ROOT_DIR, Settings
from aria_rag.corpus_mapping import MappingRule, classify_path, load_rules
from aria_rag.indexer import TABLE_CHUNKED_FILES, TABLE_ROW_MAX_LEN, Chunk, IndexedFile, load_existing_chunks, load_manifest
from aria_rag.loader import iter_pdf_paths
from aria_rag.referentiel import Piece, load_referentiel, match_piece

CHECKS_DIR = ROOT_DIR / "checks"
KNOWN_MANIFEST_DESYNC_PATH = CHECKS_DIR / "known_manifest_desync.json"
KNOWN_ZERO_CHUNK_FILES_PATH = CHECKS_DIR / "known_zero_chunk_files.json"
KNOWN_ENCODING_FAILURES_PATH = CHECKS_DIR / "known_encoding_failures.json"

Status = Literal["pass", "fail", "warn"]


@dataclass
class InvariantResult:
    name: str
    status: Status
    count: int
    message: str
    details_path: Path | None = None


def _write_details(reports_dir: Path, slug: str, payload: object) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{slug}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _relative_path(source_path: str, docs_dir: Path) -> str:
    """source_path relative to docs_dir, forward-slash — matches the
    portable format used in checks/*.json so documented paths compare
    equal regardless of which machine indexed the corpus.
    """
    try:
        return Path(source_path).resolve().relative_to(docs_dir.resolve()).as_posix()
    except ValueError:
        return source_path


# ---------------------------------------------------------------------------
# 1. Family coverage
# ---------------------------------------------------------------------------

# Families intentionally excluded from scoped-retrieval slots (config.py's
# DEFAULT_FAMILY_SLOTS) — documented, not silent.
#   - reglement_graphique, rapport_presentation: low-value for narrative
#     Q&A (see config.py's DEFAULT_FAMILY_SLOTS comment).
#   - cch: Code de la Construction et de l'Habitation (LEGIFRANCE/CCH/,
#     national norm_level) — its own family since Stage A's config-driven
#     mapping (corpus_mapping.yaml), previously fell through to "other".
#     Gets real retrieval slots in Stage C of the CCH dual-source
#     prototype; documented here as unserved until then.
# "other" is deliberately NOT here: every file under Ressources/ is now
# classified by corpus_mapping.yaml, so a chunk landing in "other" means a
# genuinely new, unmapped source was added — that must fail loudly, not
# join this list silently.
KNOWN_UNSERVED_FAMILIES = {"reglement_graphique", "rapport_presentation", "cch"}


def check_family_coverage(chunks: list[Chunk], settings: Settings, reports_dir: Path) -> InvariantResult:
    present = {c.doc_family for c in chunks}
    slotted = set(settings.family_slots)
    unserved = present - slotted
    undocumented = unserved - KNOWN_UNSERVED_FAMILIES
    offending = [c for c in chunks if c.doc_family in undocumented]

    by_family = {
        fam: {
            "chunk_count": sum(1 for c in chunks if c.doc_family == fam),
            "documented": fam in KNOWN_UNSERVED_FAMILIES,
        }
        for fam in sorted(unserved)
    }
    details_path = _write_details(reports_dir, "family_coverage", {
        "unserved_families": by_family,
        "undocumented_families": sorted(undocumented),
    })

    status: Status = "fail" if offending else "pass"
    if offending:
        message = f"{len(undocumented)} undocumented unserved famil{'y' if len(undocumented) == 1 else 'ies'}: {sorted(undocumented)}"
    else:
        message = f"{len(unserved)} unserved famil{'y' if len(unserved) == 1 else 'ies'}, all documented: {sorted(unserved)}"
    return InvariantResult("1. Family coverage", status, len(offending), message, details_path)


# ---------------------------------------------------------------------------
# 2. Size cap
# ---------------------------------------------------------------------------

# "[Section: " (10 chars) + section text (truncated to 100 chars max — see
# indexer.py's `if len(section) > 100: section = section[:97] + '...'`) +
# "]\n" (2 chars) = 112 chars, appended AFTER the chunk_size-based split —
# the one place a chunk can legitimately exceed chunk_size. An explicit,
# derived tolerance, not a silent slush factor: if this ever needs to
# change, it's because the prefix format changed, and this constant must
# change with it.
SECTION_PREFIX_MAX_LEN = 112


def _size_cap_for(chunk: Chunk, effective_cap: int) -> int:
    """Table-chunked annexe files (indexer.TABLE_CHUNKED_FILES) intentionally
    keep a single table row/building entry whole even past chunk_size —
    Annexe X's free-text "Motivation" descriptions routinely run 1300-3200
    chars for one protected building (largest observed ~5.2k), and splitting
    mid-entry would recreate the exact row-destruction bug that chunker
    exists to fix (CH-06: 2/17 addresses retrieved instead of ~17). Every
    other file keeps the ordinary cap.
    """
    if Path(chunk.source_path).name in TABLE_CHUNKED_FILES:
        return TABLE_ROW_MAX_LEN
    return effective_cap


def check_size_cap(chunks: list[Chunk], settings: Settings, reports_dir: Path) -> InvariantResult:
    effective_cap = settings.chunk_size + SECTION_PREFIX_MAX_LEN
    offending = [c for c in chunks if len(c.content) > _size_cap_for(c, effective_cap)]

    details_path = _write_details(reports_dir, "size_cap", {
        "chunk_size": settings.chunk_size,
        "section_prefix_tolerance": SECTION_PREFIX_MAX_LEN,
        "effective_cap": effective_cap,
        "table_chunked_row_max_len": TABLE_ROW_MAX_LEN,
        "offenders": [
            {"chunk_id": c.chunk_id, "source_path": c.source_path, "length": len(c.content)}
            for c in offending
        ],
    })
    status: Status = "fail" if offending else "pass"
    message = (
        f"{len(offending)} chunk(s) exceed their cap ({effective_cap} chars normally, "
        f"{TABLE_ROW_MAX_LEN} for table-chunked files: {sorted(TABLE_CHUNKED_FILES)})"
    )
    return InvariantResult("2. Size cap", status, len(offending), message, details_path)


# ---------------------------------------------------------------------------
# 3. Metadata integrity
# ---------------------------------------------------------------------------

# Lowercase-leading "annexe" as a section value — the exact signature of
# the fixed _ANNEXE_HEADER bug (see indexer.py: every historical
# false-positive section value started with a lowercase "a" before
# "nnexe"; no genuine header ever does). A pattern-class regression guard
# rather than a fixed list of past instances, so it also catches the same
# bug class recurring in NEW documents (e.g. the S3 rebuild corpus).
_ANNEXE_POLLUTION = re.compile(r'^a\s*(?i:nnexe)')


def _is_first_chunk_of_file(chunk_id: str) -> bool:
    """chunk_id is always "{stem}-{idx}", idx 0-based within its source
    file. idx==0 is the only chunk that can legitimately precede every
    structural header (front matter/title-page text) and so have no
    section — see extract_chunks_from_pdf's bisect-based section lookup.
    """
    suffix = chunk_id.rsplit("-", 1)[-1]
    return suffix.isdigit() and int(suffix) == 0


def check_metadata_integrity(chunks: list[Chunk], reports_dir: Path) -> InvariantResult:
    null_page = [c for c in chunks if c.page is None]
    null_page_end = [c for c in chunks if c.page_end is None]
    bad_order = [c for c in chunks if c.page is not None and c.page_end is not None and c.page_end < c.page]
    null_section = [
        c for c in chunks
        if c.doc_family == "reglement_ecrit" and c.section is None and not _is_first_chunk_of_file(c.chunk_id)
    ]
    annexe_pollution = [c for c in chunks if c.section and _ANNEXE_POLLUTION.match(c.section)]

    count = len(null_page) + len(null_page_end) + len(bad_order) + len(null_section) + len(annexe_pollution)
    details_path = _write_details(reports_dir, "metadata_integrity", {
        "null_page": [c.chunk_id for c in null_page],
        "null_page_end": [c.chunk_id for c in null_page_end],
        "page_end_lt_page": [{"chunk_id": c.chunk_id, "page": c.page, "page_end": c.page_end} for c in bad_order],
        "unexplained_null_section": [c.chunk_id for c in null_section],
        "annexe_pollution": [{"chunk_id": c.chunk_id, "section": c.section} for c in annexe_pollution],
    })
    status: Status = "fail" if count else "pass"
    message = (
        f"null_page={len(null_page)} null_page_end={len(null_page_end)} "
        f"page_end<page={len(bad_order)} unexplained_null_section={len(null_section)} "
        f"annexe_pollution={len(annexe_pollution)}"
    )
    return InvariantResult("3. Metadata integrity", status, count, message, details_path)


# ---------------------------------------------------------------------------
# 4. Encoding
# ---------------------------------------------------------------------------

_HARD_FAIL_CHARS = {"U+0000": chr(0x0000), "U+0002": chr(0x0002), "U+FFFD": chr(0xFFFD)}
_WARN_ONLY_CHARS = {"U+008C": chr(0x008C)}  # known leftover, not yet addressed by loader.py -- see docstring there

# Only U+FFFD may ever be suppressed by checks/known_encoding_failures.json.
# U+0000 and U+0002 were fixed corpus-wide (see loader.py's control-char
# strip) -- a reappearance of either is a regression, not a known issue, so
# no allowlist entry can suppress them even if one is mistakenly added to
# that file.
_ALLOWLISTABLE_HARD_FAIL_CHARS = {"U+FFFD"}


def check_encoding(
    chunks: list[Chunk],
    reports_dir: Path,
    settings: Settings | None = None,
    known_encoding_path: Path | None = None,
) -> InvariantResult:
    known_encoding_path = known_encoding_path or KNOWN_ENCODING_FAILURES_PATH
    known_pairs: set[tuple[str, str]] = set()
    if known_encoding_path.exists():
        data = json.loads(known_encoding_path.read_text(encoding="utf-8"))
        known_pairs = {(e["source_path"], e["char"]) for e in data.get("entries", [])}

    hard_hits: dict[str, list[str]] = {}
    documented_hits: dict[str, list[str]] = {}
    new_hits: dict[str, list[str]] = {}
    for name, ch in _HARD_FAIL_CHARS.items():
        matches = [c for c in chunks if ch in c.content]
        hard_hits[name] = [c.chunk_id for c in matches]
        documented: list[Chunk] = []
        if name in _ALLOWLISTABLE_HARD_FAIL_CHARS and settings is not None:
            documented = [
                c for c in matches
                if (_relative_path(c.source_path, settings.docs_dir), name) in known_pairs
            ]
        documented_ids = {c.chunk_id for c in documented}
        documented_hits[name] = [c.chunk_id for c in documented]
        new_hits[name] = [c.chunk_id for c in matches if c.chunk_id not in documented_ids]

    warn_hits = {name: [c.chunk_id for c in chunks if ch in c.content] for name, ch in _WARN_ONLY_CHARS.items()}
    new_count = sum(len(v) for v in new_hits.values())
    warn_count = sum(len(v) for v in warn_hits.values())

    details_path = _write_details(reports_dir, "encoding", {
        "hard_fail": hard_hits,
        "documented_hard_fail": documented_hits,
        "new_hard_fail": new_hits,
        "warn_only": warn_hits,
    })

    if new_count:
        status: Status = "fail"
    elif warn_count:
        status = "warn"
    else:
        status = "pass"

    parts = []
    for name in _HARD_FAIL_CHARS:
        if new_hits[name]:
            parts.append(f"{name}={len(new_hits[name])}")
        if documented_hits[name]:
            parts.append(f"{name}={len(documented_hits[name])} known-failure (documented, see {known_encoding_path.name})")
    hard_summary = ", ".join(parts) or "none"
    message = f"hard-fail: {hard_summary}; warn-only U+008C: {warn_count} chunk(s) (known leftover)"
    return InvariantResult("4. Encoding", status, new_count, message, details_path)


# ---------------------------------------------------------------------------
# 5. Manifest consistency
# ---------------------------------------------------------------------------

def check_manifest_consistency(
    chunks: list[Chunk],
    manifest: list[IndexedFile],
    settings: Settings,
    reports_dir: Path,
    known_desync_path: Path | None = None,
) -> InvariantResult:
    known_desync_path = known_desync_path or KNOWN_MANIFEST_DESYNC_PATH
    known_paths: set[str] = set()
    if known_desync_path.exists():
        data = json.loads(known_desync_path.read_text(encoding="utf-8"))
        known_paths = {e["source_path"] for e in data.get("entries", [])}

    actual_counts: dict[str, int] = {}
    for c in chunks:
        actual_counts[c.source_path] = actual_counts.get(c.source_path, 0) + 1

    all_desyncs = []
    new_desyncs = []
    for m in manifest:
        actual = actual_counts.get(m.source_path, 0)
        if actual != m.chunk_count:
            rel = _relative_path(m.source_path, settings.docs_dir)
            entry = {"source_path": rel, "manifest_chunk_count": m.chunk_count, "actual_chunk_count": actual}
            all_desyncs.append(entry)
            if rel not in known_paths:
                new_desyncs.append(entry)

    details_path = _write_details(reports_dir, "manifest_consistency", {
        "total_desyncs": len(all_desyncs),
        "documented_desyncs": len(all_desyncs) - len(new_desyncs),
        "new_undocumented_desyncs": new_desyncs,
        "all_desyncs": all_desyncs,
    })
    status: Status = "fail" if new_desyncs else "pass"
    message = f"{len(new_desyncs)} new desync(s), {len(all_desyncs) - len(new_desyncs)} documented pre-existing (see {known_desync_path.name})"
    return InvariantResult("5. Manifest consistency", status, len(new_desyncs), message, details_path)


# ---------------------------------------------------------------------------
# 6. Coverage
# ---------------------------------------------------------------------------

def check_coverage(
    manifest: list[IndexedFile],
    settings: Settings,
    reports_dir: Path,
    known_zero_chunk_path: Path | None = None,
    mapping_rules: list[MappingRule] | None = None,
) -> InvariantResult:
    known_zero_chunk_path = known_zero_chunk_path or KNOWN_ZERO_CHUNK_FILES_PATH
    known_zero: set[str] = set()
    if known_zero_chunk_path.exists():
        data = json.loads(known_zero_chunk_path.read_text(encoding="utf-8"))
        known_zero = set(data.get("entries", []))

    rules = mapping_rules if mapping_rules is not None else load_rules()

    manifest_paths = {m.source_path for m in manifest}
    on_disk = list(iter_pdf_paths(settings.docs_dir))
    on_disk_paths = {str(p.resolve()) for p in on_disk}
    # A validity=superseded file (corpus_mapping.yaml) is discovered but
    # deliberately never indexed — it must not be flagged as missing.
    superseded_on_disk = {
        str(p.resolve()) for p in on_disk
        if classify_path(p, settings.docs_dir, rules).validity == "superseded"
    }
    missing = sorted(on_disk_paths - manifest_paths - superseded_on_disk)

    new_zero_chunk = []
    for m in manifest:
        if m.chunk_count == 0:
            rel = _relative_path(m.source_path, settings.docs_dir)
            if rel not in known_zero:
                new_zero_chunk.append(rel)

    count = len(missing) + len(new_zero_chunk)
    details_path = _write_details(reports_dir, "coverage", {
        "on_disk_pdf_count": len(on_disk_paths),
        "manifest_entry_count": len(manifest),
        "missing_from_manifest": missing,
        "excluded_superseded": sorted(_relative_path(p, settings.docs_dir) for p in superseded_on_disk),
        "known_zero_chunk_count": len(known_zero),
        "new_unexplained_zero_chunk_files": new_zero_chunk,
    })
    status: Status = "fail" if count else "pass"
    message = (
        f"{len(missing)} PDF(s) on disk missing from manifest, {len(new_zero_chunk)} new unexplained "
        f"zero-chunk file(s) ({len(known_zero)} documented in {known_zero_chunk_path.name}), "
        f"{len(superseded_on_disk)} superseded (excluded, expected)"
    )
    return InvariantResult("6. Coverage", status, count, message, details_path)


# ---------------------------------------------------------------------------
# 7. Fragment floor
# ---------------------------------------------------------------------------

FRAGMENT_FLOOR = 30


def check_fragment_floor(chunks: list[Chunk], reports_dir: Path) -> InvariantResult:
    offending = [c for c in chunks if len(c.content) < FRAGMENT_FLOOR]
    details_path = _write_details(reports_dir, "fragment_floor", {
        "floor": FRAGMENT_FLOOR,
        "offenders": [{"chunk_id": c.chunk_id, "content": c.content} for c in offending],
    })
    # Warn-only for now — headers without body, a known issue not yet fixed.
    message = f"{len(offending)} chunk(s) under {FRAGMENT_FLOOR} chars (warn-only, known headers-without-body issue)"
    return InvariantResult("7. Fragment floor", "warn" if offending else "pass", len(offending), message, details_path)


# ---------------------------------------------------------------------------
# 8. Dedup ledger
# ---------------------------------------------------------------------------

_DEDUP_LEDGER_KEYS = {"removed_chunk_id", "removed_source_path", "kept_chunk_id", "kept_source_path", "content_hash"}


def check_dedup_ledger(settings: Settings, reports_dir: Path) -> InvariantResult:
    ledger_path = settings.index_dir / "dedup_ledger.json"
    drop_log_path = settings.index_dir / "drop_log.json"

    if not ledger_path.exists():
        return InvariantResult(
            "8. Dedup ledger", "warn", 0,
            "dedup_ledger.json not yet generated — run `aria-rag ingest` to populate it",
            None,
        )
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if not isinstance(ledger, list) or not all(_DEDUP_LEDGER_KEYS <= e.keys() for e in ledger):
            raise ValueError("unexpected schema")
    except Exception as exc:  # noqa: BLE001
        return InvariantResult("8. Dedup ledger", "fail", 0, f"dedup_ledger.json malformed: {exc}", ledger_path)

    drop_log_status = "present" if drop_log_path.exists() else "not yet generated"
    details_path = _write_details(reports_dir, "dedup_ledger", {"ledger": ledger, "drop_log_status": drop_log_status})
    return InvariantResult(
        "8. Dedup ledger", "pass", len(ledger),
        f"{len(ledger)} duplicate(s) recorded this ingest; drop_log.json {drop_log_status}",
        details_path,
    )


# ---------------------------------------------------------------------------
# 9. Referentiel coverage
# ---------------------------------------------------------------------------

def check_referentiel_coverage(
    manifest: list[IndexedFile],
    pieces: list[Piece],
    settings: Settings,
    reports_dir: Path,
) -> InvariantResult:
    """Every indexed (manifest) file must map to exactly one referentiel
    piece — an unmapped file means referentiel.yaml has fallen out of sync
    with the corpus (FAIL, loud by design, same posture as family coverage).
    Every expected=true piece with no matching file is a documented gap in
    the corpus relative to the official PLU dossier structure (WARN, not
    FAIL — an intentionally absent piece, e.g. "à confirmer" annexes, is
    known debt, not a regression).

    "every piece with present=true has >=1 file" (the spec's middle clause)
    is not separately checked: present is *derived* as "has >=1 matching
    file" (see referentiel.piece_present), so it holds by construction — the
    only way it could fail is a bug in piece_present itself, which is a unit
    test's job, not a corpus-check's.
    """
    unmapped: list[str] = []
    piece_has_file: dict[str, bool] = {p.id: False for p in pieces}
    for m in manifest:
        rel = _relative_path(m.source_path, settings.docs_dir)
        piece = match_piece(rel, pieces)
        if piece is None:
            unmapped.append(rel)
        else:
            piece_has_file[piece.id] = True

    coverage_gaps = sorted(p.id for p in pieces if p.expected and not piece_has_file.get(p.id, False))

    details_path = _write_details(reports_dir, "referentiel_coverage", {
        "unmapped_indexed_files": unmapped,
        "expected_pieces_with_no_file": coverage_gaps,
    })
    status: Status = "fail" if unmapped else ("warn" if coverage_gaps else "pass")
    count = len(unmapped)
    message = (
        f"{len(unmapped)} indexed file(s) unmapped to any referentiel piece; "
        f"{len(coverage_gaps)} expected piece(s) with no file (coverage gap): {coverage_gaps}"
    )
    return InvariantResult("9. Referentiel coverage", status, count, message, details_path)


# Matches a Chunk.section value that IS a genuine annexe title (same
# leading-uppercase convention as indexer.py's _ANNEXE_HEADER — "ANNEXE V",
# "A NNEXE V", "Annexe V"), not a lowercase prose mention.
_ANNEXE_SECTION_VALUE = re.compile(r'^A\s*(?i:NNEXE)\b')

# A source file genuinely dedicated to annexe content (e.g. REG2A10's Tome 2
# annex listings) has the overwhelming majority of its chunks so labeled.
# The 2026-07-14 boundary bug (SESSION_STATE.md) put a small MINORITY of one
# file's chunks (43/666, ~6%) under a real annexe title that belonged to a
# different tome entirely — a plausible re-occurrence of the same shape (or
# a new instance of it in a different file) would look the same: some but
# not most of a file's chunks claiming an annexe section. This is a
# heuristic, not a page-range lookup — referentiel.py's PieceFile only
# tracks a file's total page count, not per-annexe sub-ranges, so there's no
# ground truth to compare a chunk's page against; the ratio is the closest
# verifiable signal available today.
_ANNEXE_MINORITY_MAX_RATIO = 0.5


def check_annexe_section_plausibility(chunks: list[Chunk], reports_dir: Path) -> InvariantResult:
    """WARN if a reglement_ecrit source file has annexe-titled chunks
    (section matches _ANNEXE_SECTION_VALUE) but they're a MINORITY of that
    file's chunks (< _ANNEXE_MINORITY_MAX_RATIO) — the boundary-bug
    signature: a genuine annexe-dedicated file (REG2A10 series) is almost
    entirely annexe content, so a small partial contamination means some
    OTHER content (a table of contents entry, an overview paragraph, a
    cross-tome reference) is being misattributed to a real annexe title
    that doesn't actually open a section in this file.
    """
    by_file: dict[str, list[Chunk]] = {}
    for c in chunks:
        if c.doc_family == "reglement_ecrit":
            by_file.setdefault(c.source_path, []).append(c)

    suspects: dict[str, dict] = {}
    for source_path, file_chunks in by_file.items():
        annexe_chunks = [c for c in file_chunks if c.section and _ANNEXE_SECTION_VALUE.match(c.section)]
        if not annexe_chunks:
            continue
        ratio = len(annexe_chunks) / len(file_chunks)
        if ratio < _ANNEXE_MINORITY_MAX_RATIO:
            suspects[Path(source_path).name] = {
                "annexe_chunk_count": len(annexe_chunks),
                "total_chunk_count": len(file_chunks),
                "ratio": round(ratio, 3),
                "sample_chunk_ids": [c.chunk_id for c in annexe_chunks[:5]],
                "sample_sections": sorted({c.section for c in annexe_chunks})[:5],
            }

    details_path = _write_details(reports_dir, "annexe_section_plausibility", {"suspect_files": suspects})
    status: Status = "warn" if suspects else "pass"
    count = sum(s["annexe_chunk_count"] for s in suspects.values())
    message = (
        f"{len(suspects)} file(s) with a minority annexe-section contamination: {sorted(suspects)}"
        if suspects else "no file has a minority-only annexe-section pattern"
    )
    return InvariantResult("10. Annexe section plausibility", status, count, message, details_path)


# ---------------------------------------------------------------------------
# Report + entry point
# ---------------------------------------------------------------------------

_STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "warn": "WARN"}


def print_report(results: list[InvariantResult]) -> None:
    name_w = max(len(r.name) for r in results) + 2
    print(f"{'Invariant':<{name_w}}{'Status':<8}{'Count':<8}Details")
    print("-" * (name_w + 8 + 8 + 50))
    for r in results:
        details = str(r.details_path) if r.details_path else "-"
        print(f"{r.name:<{name_w}}{_STATUS_LABEL[r.status]:<8}{r.count:<8}{details}")
        print(f"    {r.message}")
    print("-" * (name_w + 8 + 8 + 50))
    n_fail = sum(1 for r in results if r.status == "fail")
    n_warn = sum(1 for r in results if r.status == "warn")
    print(f"{n_fail} FAIL, {n_warn} WARN, {len(results) - n_fail - n_warn} PASS")


def run_checks(settings: Settings, strict: bool = False) -> list[InvariantResult]:
    chunks_by_source = load_existing_chunks(settings.index_dir)
    chunks = [c for source_chunks in chunks_by_source.values() for c in source_chunks]
    manifest = list(load_manifest(settings.index_dir).values())
    reports_dir = settings.index_dir / "check_reports"
    pieces = load_referentiel().pieces

    results = [
        check_family_coverage(chunks, settings, reports_dir),
        check_size_cap(chunks, settings, reports_dir),
        check_metadata_integrity(chunks, reports_dir),
        check_encoding(chunks, reports_dir, settings),
        check_manifest_consistency(chunks, manifest, settings, reports_dir),
        check_coverage(manifest, settings, reports_dir),
        check_fragment_floor(chunks, reports_dir),
        check_dedup_ledger(settings, reports_dir),
        check_referentiel_coverage(manifest, pieces, settings, reports_dir),
        check_annexe_section_plausibility(chunks, reports_dir),
    ]
    print_report(results)

    if strict and any(r.status == "fail" for r in results):
        raise SystemExit(f"aria-rag check --strict: {sum(1 for r in results if r.status == 'fail')} invariant(s) failed")

    return results
