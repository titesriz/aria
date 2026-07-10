"""One-off: backfill Chunk.page_end onto every chunk already in
data/index/chunks.json, without re-embedding or touching FAISS/BM25.

Provably equivalent to a full re-ingest for this migration specifically:
chunking (chunk_size/chunk_overlap/min_alpha_ratio, and chunk_id assignment
order) is fully deterministic and unchanged, so re-running only
extract_chunks_from_pdf (cheap — pypdf read + regex chunking, no embedding)
reproduces byte-identical (source_path, chunk_id) -> content pairs to what's
already indexed, plus the new page_end field. Content equality is asserted
per chunk as a safety check before trusting the match; any mismatch aborts
rather than silently writing wrong metadata.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
from aria_rag.config import load_settings
from aria_rag.corpus_mapping import classify_path, load_rules
from aria_rag.indexer import extract_chunks_from_pdf, iter_pdf_paths

settings = load_settings()
chunks_path = settings.index_dir / "chunks.json"
existing = json.loads(chunks_path.read_text(encoding="utf-8"))
print(f"Existing chunks: {len(existing)}", flush=True)

existing_by_key: dict[tuple[str, str], dict] = {
    (c["source_path"], c["chunk_id"]): c for c in existing
}

pdf_paths = iter_pdf_paths(settings.docs_dir)
mapping_rules = load_rules()
print(f"Re-extracting (chunking only, no embedding) {len(pdf_paths)} files...", flush=True)

t0 = time.time()
matched = 0
mismatched: list[str] = []
missing: list[str] = []

for i, path in enumerate(pdf_paths, start=1):
    classification = classify_path(path, settings.docs_dir, mapping_rules)
    fresh_chunks, _ = extract_chunks_from_pdf(
        path, settings.chunk_size, settings.chunk_overlap, settings.min_alpha_ratio, classification
    )
    for fc in fresh_chunks:
        key = (fc.source_path, fc.chunk_id)
        existing_chunk = existing_by_key.get(key)
        if existing_chunk is None:
            continue  # dropped by cross-file dedup in the original ingest — not in chunks.json, fine
        if existing_chunk["content"] != fc.content:
            mismatched.append(fc.chunk_id)
            continue
        existing_chunk["page_end"] = fc.page_end
        matched += 1
    if i % 50 == 0:
        print(f"  ...{i}/{len(pdf_paths)}", flush=True)

elapsed = time.time() - t0
print(f"\nDone in {elapsed:.1f}s")
print(f"Matched + backfilled page_end: {matched}")
print(f"Content mismatches (would need investigation): {len(mismatched)}")
if mismatched:
    print("  ", mismatched[:10])

still_missing = [c["chunk_id"] for c in existing if "page_end" not in c]
print(f"Existing chunks still without page_end after migration: {len(still_missing)}")
if still_missing:
    print("  ", still_missing[:10])

if mismatched:
    print("\nABORTING WRITE — content mismatches found, investigate before proceeding.")
    sys.exit(1)

chunks_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nWrote {chunks_path}")
