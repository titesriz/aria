"""Read-only equivalence proof for the 2026-09-19 embedding-cache change
(indexer.py's _load_embedding_cache + the cache-aware embedding step in
build_index). Deliberately does NOT go through build_index()/the CLI --
a separate, pre-existing bug (manifest chunk_count desync silently
triggering full re-extraction via the old pypdf path, regardless of
--family) makes that path unsafe to exercise against the real corpus right
now. This script only reads data/index/, never writes to it.

Three checks:
  1. Cache-hit correctness at full corpus scale: every current chunk's
     content-hash lookup returns a vector bit-identical to reconstruct(i)
     at its OWN position -- proves the hash+reconstruct mechanism is sound
     against the real 27k-chunk index, not a toy example.
  2. Full-corpus, nothing-changed replay: run the exact embedding-selection
     logic from build_index on the CURRENT chunks list unchanged, and prove
     the resulting embeddings array is byte-identical to what's already in
     index.faiss -- this is the "12-case retrieval output is identical"
     proof, since retrieval depends only on this array (BM25 side is
     untouched by this change, same full rebuild as always).
  3. Genuine change is still detected: mutate one real chunk's content in a
     copy, confirm ONLY that chunk misses the cache and gets freshly
     encoded (with a different vector), while every other chunk still
     reuses its exact original vector.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from aria_rag.config import Settings
from aria_rag.indexer import Chunk, _load_embedding_cache

settings = Settings()

print("=== Loading current index (read-only) ===")
chunks_raw = json.loads((settings.index_dir / "chunks.json").read_text(encoding="utf-8"))
chunks = [Chunk(**item) for item in chunks_raw]
index = faiss.read_index(str(settings.index_dir / "index.faiss"))
print(f"{len(chunks)} chunks, index.ntotal={index.ntotal}")
assert index.ntotal == len(chunks)

# --- Check 1: cache-hit correctness at full scale --------------------------
print("\n=== Check 1: hash+reconstruct correctness (full corpus) ===")
t0 = time.time()
cache = _load_embedding_cache(settings.index_dir)
t1 = time.time()
print(f"_load_embedding_cache: {len(cache)} distinct content-hashes in {t1-t0:.2f}s")

mismatches = 0
checked = 0
for i, c in enumerate(chunks):
    h = hashlib.md5(c.content.encode()).hexdigest()
    if h not in cache:
        mismatches += 1
        continue
    own_vec = index.reconstruct(i)
    cached_vec = cache[h]
    checked += 1
    if not np.array_equal(own_vec, cached_vec):
        mismatches += 1
print(f"checked={checked}/{len(chunks)}, hash-not-in-own-cache={len(chunks)-checked}, vector mismatches={mismatches}")
assert mismatches == 0, "Check 1 FAILED"
print("Check 1 PASSED: every chunk's cache-hit vector matches its own stored vector exactly.")

# --- Check 2: full replay, nothing changed ---------------------------------
print("\n=== Check 2: replay build_index's embedding step, nothing changed ===")
t0 = time.time()
chunk_hashes = [hashlib.md5(c.content.encode()).hexdigest() for c in chunks]
to_encode_positions = [i for i, h in enumerate(chunk_hashes) if h not in cache]
print(f"to_encode_positions: {len(to_encode_positions)} (expect 0 -- nothing changed)")

dimension = next(iter(cache.values())).shape[0]
new_embeddings = np.empty((len(chunks), dimension), dtype=np.float32)
for i in range(len(chunks)):
    new_embeddings[i] = cache[chunk_hashes[i]]
t1 = time.time()
print(f"Rebuilt full embeddings array from cache alone in {t1-t0:.2f}s (no model.encode() call needed)")

original_embeddings = index.reconstruct_n(0, index.ntotal)
identical = np.array_equal(new_embeddings, original_embeddings)
print(f"New embeddings array byte-identical to current index.faiss: {identical}")
assert identical, "Check 2 FAILED"

# Build a fresh FAISS index from the replayed array and confirm IDENTICAL search results
fresh_index = faiss.IndexFlatIP(dimension)
fresh_index.add(new_embeddings)
model = SentenceTransformer(settings.embedding_model)
test_queries = [
    "Quelle est la hauteur maximale en zone UG ?",
    "Je souhaite remplacer mon toit en zinc par un toit en tuile.",
    "Liste des adresses du 1er arrondissement emplacements reserves logement",
]
for q in test_queries:
    qv = np.array(model.encode([q], normalize_embeddings=True), dtype=np.float32)
    s1, i1 = index.search(qv, 10)
    s2, i2 = fresh_index.search(qv, 10)
    same_ids = np.array_equal(i1, i2)
    same_scores = np.allclose(s1, s2, atol=1e-6)
    print(f"  query={q[:50]!r}: same top-10 ids={same_ids}, same scores={same_scores}")
    assert same_ids and same_scores
print("Check 2 PASSED: replayed index is byte-identical and produces identical search results for real queries.")

# --- Check 3: a genuine change is still detected ---------------------------
print("\n=== Check 3: one real chunk mutated -- must miss cache and re-encode ===")
# Pick a short chunk (well under the model's truncation window) so the
# mutation is guaranteed to reach the actual embedding, not get silently
# truncated away -- a 1200-char (max chunk_size) victim already showed this
# exact failure mode once (see log): appending text past truncation left
# the vector genuinely, correctly unchanged, which is a model-truncation
# property, not a cache bug, but makes a weak test.
victim_idx = min(range(len(chunks)), key=lambda i: len(chunks[i].content) if len(chunks[i].content) > 20 else 10**9)
mutated_chunks_content = list(c.content for c in chunks)
original_text = mutated_chunks_content[victim_idx]
print(f"victim_idx={victim_idx}, original len={len(original_text)} chars: {original_text!r}")
mutated_text = "Ceci est un texte completement different pour le test de mutation. " + original_text
mutated_chunks_content[victim_idx] = mutated_text

mutated_hashes = [hashlib.md5(t.encode()).hexdigest() for t in mutated_chunks_content]
to_encode = [i for i, h in enumerate(mutated_hashes) if h not in cache]
print(f"to_encode after mutating 1 chunk: {to_encode} (expect exactly [{victim_idx}])")
assert to_encode == [victim_idx], "Check 3 FAILED: wrong set of chunks flagged for re-encoding"

fresh_vec = np.array(model.encode([mutated_text], normalize_embeddings=True), dtype=np.float32)[0]
old_vec = index.reconstruct(victim_idx)
different = not np.array_equal(fresh_vec, old_vec)
print(f"Freshly-encoded mutated vector differs from the old stale vector: {different}")
assert different, "Check 3 FAILED: mutated content produced the same vector (suspicious)"
print("Check 3 PASSED: exactly one chunk detected as changed, correctly re-encoded, and the new vector differs.")

print("\n=== ALL CHECKS PASSED ===")
