"""Patch doc_family in chunks.json and rebuild BM25 — no re-embedding needed."""
from __future__ import annotations

import json
import pickle
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_DIR = ROOT / "data" / "index"

DOC_FAMILIES = {
    "Règlement/Pièces écrites": "reglement_ecrit",
    "Règlement/Documents graphiques": "reglement_graphique",
    "Rapport de présentation": "rapport_presentation",
    "OAP": "oap",
    "PADD": "padd",
    "Annexes": "annexes",
}


def infer_doc_family(source_path: str) -> str:
    path_str = unicodedata.normalize("NFC", source_path)
    for fragment, family in DOC_FAMILIES.items():
        if fragment in path_str:
            return family
    return "other"


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def main() -> None:
    chunks_path = INDEX_DIR / "chunks.json"
    bm25_path = INDEX_DIR / "bm25.pkl"

    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

    before = {c["doc_family"] for c in chunks}
    for chunk in chunks:
        chunk["doc_family"] = infer_doc_family(chunk["source_path"])
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
