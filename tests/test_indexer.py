"""Synthetic, fixture-only tests for build_index's per-file cache-reuse
decision (should_reuse_cached_chunks) -- no real PDFs, no embedding model,
no FAISS, and NEVER touches the real corpus (data/index/, Ressources/).

Covers the 2026-09-19 incident: `aria-rag ingest --rebuild --family
reglement_graphique` silently re-extracted REG1_MS1.pdf/REG2A1_MS1.pdf/both
REG2A10 files (family=reglement_ecrit, outside the requested family) via the
old pypdf path, discarding their Docling chunks, because their manifest
chunk_count had desynced from chunks.json's actual post-dedup count (a
known, pre-existing debt class -- see known_manifest_desync.json) and the
old per-file cache check didn't distinguish "should we force reprocessing"
(correctly family-scoped) from "is the cache bookkeeping internally
consistent" (not family-scoped at all, so a desync anywhere fell through to
full re-extraction regardless of --family).
"""
from aria_rag.indexer import IndexedFile, should_reuse_cached_chunks


def _old_buggy_logic(*, force_this_file: bool, cached, cached_chunk_count: int, size_bytes: int, modified_time: float) -> bool:
    """The exact pre-fix boolean expression (indexer.py, before 2026-09-19)
    -- reproduced here, not imported, so this test keeps proving the OLD
    behavior even after the source no longer contains it. Used to show the
    incident scenario really did trigger reprocessing under the old logic.
    """
    return (
        not force_this_file
        and cached is not None
        and cached.size_bytes == size_bytes
        and cached.modified_time == modified_time
        and cached_chunk_count == cached.chunk_count
    )


def test_out_of_family_desynced_file_untouched_by_new_logic_but_broken_by_old():
    """The exact 2026-09-19 incident shape: REG1_MS1.pdf, family=reglement_ecrit,
    NOT in the requested --family reglement_graphique filter, on-disk file
    UNCHANGED (same size/mtime) but its manifest.chunk_count (655, pre-dedup)
    disagrees with its actual cached chunk count (653, post cross-file
    dedup) -- the desync class known_manifest_desync.json documents.
    """
    cached = IndexedFile(source_path="REG1_MS1.pdf", size_bytes=5_926_863, modified_time=1_775_088_152.0, chunk_count=655)
    cached_chunk_count = 653  # len(existing_chunks["REG1_MS1.pdf"]) -- desynced from cached.chunk_count
    size_bytes, modified_time = 5_926_863, 1_775_088_152.0  # file genuinely unchanged on disk
    in_family_scope = False  # classify_path(...).family == "reglement_ecrit", filter was ["reglement_graphique"]
    force_this_file = False  # rebuild=True but in_family_scope=False -> force_this_file = rebuild and in_family_scope

    old_would_reuse = _old_buggy_logic(
        force_this_file=force_this_file, cached=cached, cached_chunk_count=cached_chunk_count,
        size_bytes=size_bytes, modified_time=modified_time,
    )
    assert old_would_reuse is False, (
        "sanity check: the OLD logic must fail to reuse here -- this is exactly what let "
        "`--family reglement_graphique` silently re-extract REG1_MS1.pdf on 2026-09-19"
    )

    new_would_reuse = should_reuse_cached_chunks(
        in_family_scope=in_family_scope, force_this_file=force_this_file, cached=cached,
        cached_chunk_count=cached_chunk_count, size_bytes=size_bytes, modified_time=modified_time,
    )
    assert new_would_reuse is True, "the FIX: an out-of-family file must be reused untouched regardless of desync"


def test_in_family_desynced_file_still_gets_reprocessed():
    """A file INSIDE the requested family with a desync should still be
    reprocessed (the fix must not silently paper over real corpus issues
    for files actually in scope -- it only protects out-of-scope files).
    """
    cached = IndexedFile(source_path="PADD.pdf", size_bytes=1000, modified_time=100.0, chunk_count=180)
    result = should_reuse_cached_chunks(
        in_family_scope=True, force_this_file=True, cached=cached, cached_chunk_count=176,
        size_bytes=1000, modified_time=100.0,
    )
    assert result is False


def test_in_family_unchanged_consistent_file_reused_without_rebuild():
    """Baseline: normal incremental mode (no --rebuild), unchanged file,
    consistent bookkeeping -- must still reuse the cache exactly as before
    the fix (no regression to ordinary incremental behavior).
    """
    cached = IndexedFile(source_path="OAP_SANTE.pdf", size_bytes=500, modified_time=50.0, chunk_count=40)
    result = should_reuse_cached_chunks(
        in_family_scope=True, force_this_file=False, cached=cached, cached_chunk_count=40,
        size_bytes=500, modified_time=50.0,
    )
    assert result is True


def test_out_of_family_file_with_no_prior_cache_still_processed():
    """A brand-new file (never indexed, cached is None) outside the
    requested family is NOT covered by the "leave as-is" contract -- there
    is nothing to reuse, so it still proceeds to extraction. Confirms the
    fix doesn't accidentally make --family swallow newly-discovered files.
    """
    result = should_reuse_cached_chunks(
        in_family_scope=False, force_this_file=False, cached=None, cached_chunk_count=0,
        size_bytes=123, modified_time=45.0,
    )
    assert result is False


def test_out_of_family_file_with_consistent_cache_also_reused():
    """Out-of-scope + no desync at all: must also be reused (the common
    case, not just the desync edge case) -- --family never touches files
    outside it, full stop.
    """
    cached = IndexedFile(source_path="ANN9.pdf", size_bytes=8_394_000, modified_time=200.0, chunk_count=178)
    result = should_reuse_cached_chunks(
        in_family_scope=False, force_this_file=False, cached=cached, cached_chunk_count=178,
        size_bytes=8_394_000, modified_time=200.0,
    )
    assert result is True


def test_in_family_changed_on_disk_file_reprocessed():
    """A file INSIDE scope whose size/mtime changed on disk (genuinely
    edited) must still be reprocessed even without --rebuild forcing it --
    this is the ordinary incremental "did the source change" signal and
    must be untouched by the --family fix.
    """
    cached = IndexedFile(source_path="OAP_METROPOLE.pdf", size_bytes=1000, modified_time=100.0, chunk_count=22)
    result = should_reuse_cached_chunks(
        in_family_scope=True, force_this_file=False, cached=cached, cached_chunk_count=22,
        size_bytes=1500, modified_time=999.0,  # file changed on disk
    )
    assert result is False
