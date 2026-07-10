"""Patch doc_family (+ norm_level/city) in chunks.json and rebuild BM25 —
no re-embedding needed. Reads classification from corpus_mapping.yaml, the
same source of truth indexer.py uses — this script no longer carries its
own copy of the classification rules (it used to duplicate the old
hardcoded DOC_FAMILIES dict, which silently went stale when that dict was
replaced by the config-driven mapping in Stage A of the CCH prototype).
"""
from __future__ import annotations

import json
import pickle
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_DIR = ROOT / "data" / "index"
DOCS_DIR = ROOT / "Ressources"

sys.path.insert(0, str(ROOT / "src"))
from aria_rag.corpus_mapping import classify_path, load_rules


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def main() -> None:
    chunks_path = INDEX_DIR / "chunks.json"
    bm25_path = INDEX_DIR / "bm25.pkl"

    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    rules = load_rules()

    before = {c["doc_family"] for c in chunks}
    for chunk in chunks:
        classification = classify_path(Path(chunk["source_path"]), DOCS_DIR, rules)
        chunk["doc_family"] = classification.family
        chunk["norm_level"] = classification.norm_level
        chunk["city"] = classification.city
    after = {c["doc_family"] for c in chunks}

    counts = {}
    for c in chunks:
        counts[c["doc_family"]] = counts.get(c["doc_family"], 0) + 1

    chunks_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"chunks.json updated ({len(chunks)} chunks)")
    for family, n in sorted(counts.items()):
        print(f"  {family}: {n}")

    print("Rebuilding BM25...", end=" ", flush=True)
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi([tokenize(c["content"]) for c in chunks])
    with open(bm25_path, "wb") as f:
        pickle.dump(bm25, f)
    print("done.")
    print("FAISS index unchanged — no re-embedding needed.")


if __name__ == "__main__":
    main()
