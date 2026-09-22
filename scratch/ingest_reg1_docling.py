"""Section-aware live-index write for REG1_MS1.pdf (règlement écrit Tome 1) —
Docling structural extraction (scratch/extract_reg1_content.py's output)
replaces this ONE file's chunks in the live FAISS/BM25 index, so every
chunk's `section` is the real article code (UG.3.1.2, etc.) instead of
None. Every other file's existing chunks are carried through byte-for-byte
unchanged; BM25/FAISS have no true incremental-add API in this codebase
(build_index always re-embeds the full corpus), so a full re-embed is
required, but no other file's content, section, or family changes.

Classification note: on 2026-09-18/19 corpus_mapping.yaml was briefly
changed to key on "PLU bioclimatique/..." based on one machine's local
Ressources/ tree only; a 2026-09-19 cross-machine check (git-tracked
eval/ontology/corpus_pdf_inventory_full.csv's chemin_relatif rows +
scratch/reconcile.py, both predating that change) confirmed the real,
portable tree is "PLU/75 Paris/PLU Bioclimatique/..." — the original form —
and the yaml was reverted back. DOC_FAMILY/NORM_LEVEL/CITY below come from
a live classify_path() call rather than being hardcoded, so this script
tracks whichever form corpus_mapping.yaml currently uses without needing
its own fix; only PDF_PATH below is a literal, machine-specific path and
needs to match Settings.docs_dir's real layout on whatever machine runs
this.

Design mirrors indexer.py's chunk_text_by_article exactly (split at
article-code header boundaries, no overlap, oversized articles split by
character) -- just driven by Docling's section_header list instead of
regex over pypdf text. A section_header that ISN'T a bare article code
(PARTIE/ZONE/lettered A-G front matter, "IV. DÉFINITIONS" entries, etc.)
does NOT open a new chunk -- its text folds into the current run, exactly
like the regex chunker's own inline-code-only convention -- so `section`
never becomes a truncated non-code fragment.

Run: python3 scratch/ingest_reg1_docling.py
"""
from __future__ import annotations

import hashlib
import json
import pickle
import re
import sys
import unicodedata
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from aria_rag.config import Settings
from aria_rag.corpus_mapping import classify_path, load_rules
from aria_rag.indexer import Chunk, IndexedFile, _tokenize, get_file_signature, build_article_whitelist

CONTENT_JSON = REPO_ROOT / "scratch" / "reg1_content.json"
PDF_PATH = REPO_ROOT / "Ressources" / "PLU" / "75 Paris" / "PLU Bioclimatique" / "Règlement" / "Pièces écrites" / "Tome 1" / "REG1_MS1.pdf"
SOURCE_PATH = str(PDF_PATH)
STEM = "REG1_MS1"

CHUNK_SIZE = 1200  # matches Settings.chunk_size / chunk_text_by_article's own default
_settings_for_classification = Settings()
_classification = classify_path(PDF_PATH, _settings_for_classification.docs_dir, load_rules())
assert _classification.family == "reglement_ecrit" and _classification.validity == "current", (
    f"REG1_MS1.pdf classified as {_classification} — expected reglement_ecrit/current; "
    "corpus_mapping.yaml drifted again, fix it before running this script."
)
DOC_FAMILY = _classification.family
NORM_LEVEL = _classification.norm_level
CITY = _classification.city
MIN_ALPHA_RATIO = 0.5

_ARTICLE_CODE_RE = re.compile(r'^((?:UG(?:SU)?|UV|N|A|P)\w*\.\d+(?:\.\d+)*)\b')


def _alpha_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if c.isalpha()) / len(text)


def load_texts() -> list[dict]:
    doc = json.loads(CONTENT_JSON.read_text(encoding="utf-8"))
    return doc["texts"]


def group_by_article(texts: list[dict]) -> list[tuple[str | None, list[dict], list[str]]]:
    """(article_code_or_None, text_items, flagged_headers) runs. A
    section_header opens a new run only when it matches an article-code
    prefix; other headers (front matter, PARTIE/ZONE titles) are folded
    into the current run's body text, exactly like the live regex
    chunker's inline-match-only convention. `flagged_headers` collects any
    section_header text this run absorbed as body content, purely for the
    ingestion report (step 2's "doesn't look like a clean article code").
    """
    groups: list[tuple[str | None, list[dict], list[str]]] = []
    current_code: str | None = None
    current_items: list[dict] = []
    current_flagged: list[str] = []
    for item in texts:
        label = item.get("label")
        if label == "page_footer":
            continue
        if label == "section_header":
            text = item.get("text", "").strip()
            m = _ARTICLE_CODE_RE.match(text)
            if m:
                if current_items:
                    groups.append((current_code, current_items, current_flagged))
                current_code = m.group(1)
                current_items = [item]
                current_flagged = []
                continue
            else:
                current_flagged.append(text)
        current_items.append(item)
    if current_items:
        groups.append((current_code, current_items, current_flagged))
    return groups


def build_chunks() -> tuple[list[Chunk], list[dict], dict]:
    texts = load_texts()
    groups = group_by_article(texts)

    chunks: list[Chunk] = []
    drops: list[dict] = []
    report_sections: dict[str, dict] = {}
    idx = 0
    for code, items, flagged in groups:
        text = "\n".join(i["text"].strip() for i in items if i.get("text", "").strip())
        if not text:
            continue
        pages = [i["prov"][0]["page_no"] for i in items if i.get("prov")]
        page = min(pages) if pages else None
        page_end = max(pages) if pages else None

        if len(text) <= CHUNK_SIZE:
            pieces = [text]
        else:
            pieces = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]

        for piece in pieces:
            ratio = _alpha_ratio(piece)
            if ratio < MIN_ALPHA_RATIO:
                drops.append({
                    "type": "low_alpha", "source_path": SOURCE_PATH, "chunk_index": idx,
                    "alpha_ratio": round(ratio, 3), "section": code,
                    "content_preview": piece[:80].replace("\n", " "),
                })
                idx += 1
                continue
            chunks.append(Chunk(
                chunk_id=f"{STEM}-{idx}",
                source_path=SOURCE_PATH,
                doc_family=DOC_FAMILY,
                content=piece,
                page=page,
                page_end=page_end,
                section=code,
                norm_level=NORM_LEVEL,
                city=CITY,
                chunk_type=None,
            ))
            info = report_sections.setdefault(code, {"n_chunks": 0, "lengths": []})
            info["n_chunks"] += 1
            info["lengths"].append(len(piece))
            idx += 1
        if flagged:
            report_sections.setdefault("__non_article_headers_folded__", {"n_chunks": 0, "lengths": [], "headers": []})
            report_sections["__non_article_headers_folded__"].setdefault("headers", []).extend(flagged)

    return chunks, drops, report_sections


def main():
    settings = Settings()
    new_chunks, drops, report_sections = build_chunks()
    print(f"Docling section-aware chunking of REG1_MS1.pdf: {len(new_chunks)} chunks kept, {len(drops)} dropped (low-alpha)")
    none_section = sum(1 for c in new_chunks if c.section is None)
    print(f"section=None: {none_section} / {len(new_chunks)}")

    # --- Load existing index state, replace ONLY this file's chunks ---
    # NFC-normalized comparison: the on-disk chunks.json/manifest.json store
    # source_path exactly as os.walk/pathlib.iterdir() returned it on this
    # Mac, which is NFD for accented path components ("Règlement", "Pièces
    # écrites") -- discovered live (see report) when a naive `==` against
    # this script's own NFC string literal matched ZERO of the 666 existing
    # REG1_MS1.pdf chunks, which would have left them duplicated alongside
    # the new ones instead of replaced. Match on NFC-normalized form, but
    # keep writing the corpus's existing (NFD) source_path string for both
    # the new chunks and the manifest entry, so every OTHER file's
    # source_path convention — and any future incremental rebuild's cache
    # lookup against manifest.json — stays consistent.
    chunks_path = settings.index_dir / "chunks.json"
    manifest_path = settings.index_dir / "manifest.json"
    existing_raw = json.loads(chunks_path.read_text(encoding="utf-8"))
    existing_chunks = [Chunk(**item) for item in existing_raw]
    target_nfc = unicodedata.normalize("NFC", SOURCE_PATH)
    matches = [c for c in existing_chunks if unicodedata.normalize("NFC", c.source_path) == target_nfc]
    before_count = len(matches)
    on_disk_source_path = matches[0].source_path if matches else SOURCE_PATH
    for c in new_chunks:
        c.source_path = on_disk_source_path
    kept_other_chunks = [c for c in existing_chunks if unicodedata.normalize("NFC", c.source_path) != target_nfc]
    print(f"Replacing {before_count} stale chunks for REG1_MS1.pdf with {len(new_chunks)} new ones "
          f"(other files' {len(kept_other_chunks)} chunks untouched)")

    all_chunks = kept_other_chunks + new_chunks

    # Dedup (same rule as build_index: identical content hash across the corpus)
    seen: dict[str, Chunk] = {}
    unique_chunks: list[Chunk] = []
    dedup_ledger: list[dict] = []
    for c in all_chunks:
        h = hashlib.md5(c.content.encode()).hexdigest()
        kept = seen.get(h)
        if kept is None:
            seen[h] = c
            unique_chunks.append(c)
        else:
            dedup_ledger.append({
                "removed_chunk_id": c.chunk_id, "removed_source_path": c.source_path,
                "kept_chunk_id": kept.chunk_id, "kept_source_path": kept.source_path,
                "content_hash": h,
            })
    all_chunks = unique_chunks
    all_chunks.sort(key=lambda c: (c.source_path, c.chunk_id))

    print(f"Building embeddings with {settings.embedding_model} for {len(all_chunks)} chunks (full re-embed — BM25/FAISS aren't incremental in this codebase)...")
    model = SentenceTransformer(settings.embedding_model)
    texts_ = [c.content for c in all_chunks]
    embeddings = model.encode(texts_, batch_size=64, show_progress_bar=True, normalize_embeddings=True)
    embeddings = np.array(embeddings, dtype=np.float32)

    dimension = embeddings.shape[1]
    faiss_index = faiss.IndexFlatIP(dimension)
    faiss_index.add(embeddings)

    print("Building BM25 index...")
    bm25 = BM25Okapi([_tokenize(c.content) for c in all_chunks])
    with open(settings.index_dir / "bm25.pkl", "wb") as f:
        pickle.dump(bm25, f)
    faiss.write_index(faiss_index, str(settings.index_dir / "index.faiss"))
    chunks_path.write_text(json.dumps([asdict(c) for c in all_chunks], ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Manifest: update only REG1_MS1.pdf's entry ---
    existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    size_bytes, modified_time = get_file_signature(PDF_PATH)  # Path.stat() works regardless of NFC/NFD
    new_entry = {
        "source_path": on_disk_source_path, "size_bytes": size_bytes,
        "modified_time": modified_time, "chunk_count": len(new_chunks),
    }
    manifest_entries = [
        m for m in existing_manifest
        if unicodedata.normalize("NFC", m["source_path"]) != target_nfc
    ] + [new_entry]
    manifest_entries.sort(key=lambda m: m["source_path"])
    manifest_path.write_text(json.dumps(manifest_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Drop log / dedup ledger: this run's only (matches build_index's own "reflects only files reprocessed this run" convention) ---
    (settings.index_dir / "drop_log.json").write_text(json.dumps(drops, ensure_ascii=False, indent=2), encoding="utf-8")
    (settings.index_dir / "dedup_ledger.json").write_text(json.dumps(dedup_ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    whitelist = build_article_whitelist(all_chunks)
    from aria_rag.indexer import ARTICLE_WHITELIST_PATH
    ARTICLE_WHITELIST_PATH.write_text(json.dumps(whitelist, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(whitelist)} article codes to {ARTICLE_WHITELIST_PATH}")

    # --- Section report (step 2 checkpoint) ---
    report_path = REPO_ROOT / "scratch" / "reg1_section_report.json"
    report_path.write_text(json.dumps(report_sections, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote section report to {report_path}")
    print("DONE")


if __name__ == "__main__":
    main()
