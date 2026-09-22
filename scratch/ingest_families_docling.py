"""Generalized Docling section-aware live-index write for a batch of files —
same method as scratch/ingest_reg1_docling.py (Docling structural extraction,
split on section_header boundaries, split oversized, no overlap, low-alpha
drop, full re-embed, other files' chunks untouched), generalized from
REG1_MS1's article-code-only header filter to accept ANY Docling
section_header as a boundary — these families (annexes/oap/rapport_
presentation/padd/cch) have no article-code convention, so there is no
narrower signal to filter on.

Deliberately NOT enhanced beyond that: Docling's export_to_dict() puts table
content in a separate top-level "tables" key, not inline in "texts" — this
script reads only "texts" (identical to REG1_MS1's own convention), so a
table-structured page contributes NOTHING to its chunks (not even a
null-section placeholder) unless there's also surrounding prose text on the
same page. This is intentional for this pass: report where it shows up
(section report below), don't add table extraction to compensate — that's a
chunker-tuning decision for a separate pass.

Per-file resilience: each PDF is Docling-extracted in ITS OWN subprocess
(scratch/docling_extract_one.py) with a wall-clock timeout, so one hanging
or crashing file cannot take the rest of the batch down with it. A
timed-out/failed file's EXISTING chunks are left untouched (not deleted) —
same "never silently drop a file's content" principle as build_index's own
error handling.

Usage: python3 scratch/ingest_families_docling.py <stage_name> <family[,family...]> \
           [--timeout SECONDS] [--exclude FILENAME[,FILENAME...]] [--only FILENAME[,FILENAME...]]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import subprocess
import sys
import time
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
from aria_rag.indexer import Chunk, _tokenize, get_file_signature, build_article_whitelist

CHUNK_SIZE = 1200
MIN_ALPHA_RATIO = 0.5
DOCLING_CACHE = REPO_ROOT / "scratch" / "docling_cache"
WORKER = REPO_ROOT / "scratch" / "docling_extract_one.py"


def _alpha_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if c.isalpha()) / len(text)


def extract_one(pdf_path: Path, family: str, timeout: int) -> tuple[str, dict | None, str]:
    """Returns (status, content_dict_or_None, message). status in
    {"ok", "cached", "timeout", "error"}."""
    out_path = DOCLING_CACHE / family / f"{pdf_path.stem}.json"
    if out_path.exists():
        try:
            return "cached", json.loads(out_path.read_text(encoding="utf-8")), f"reused {out_path}"
        except Exception as exc:
            pass  # fall through and re-extract if the cached file is corrupt
    t0 = time.time()
    import os
    env = dict(os.environ)
    # Windows-only: huggingface_hub's cache layer defaults to symlinking a
    # downloaded model blob into its snapshot dir, which needs
    # SeCreateSymbolicLinkPrivilege (Developer Mode or admin) -- absent here,
    # first-download fails with WinError 1314. This makes it copy instead.
    env["HF_HUB_DISABLE_SYMLINKS"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, str(WORKER), str(pdf_path), str(out_path)],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", env=env,
        )
    except subprocess.TimeoutExpired:
        return "timeout", None, f"exceeded {timeout}s budget"
    dt = time.time() - t0
    if proc.returncode != 0:
        return "error", None, f"exit {proc.returncode} after {dt:.0f}s: {(proc.stderr or proc.stdout)[-500:]}"
    if not out_path.exists():
        return "error", None, f"worker exited 0 but wrote no output ({dt:.0f}s)"
    return "ok", json.loads(out_path.read_text(encoding="utf-8")), proc.stdout.strip()


def group_by_heading(texts: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """(section_header_text_or_None, text_items) runs. EVERY Docling
    section_header opens a new run (unlike REG1_MS1's article-code-only
    filter — these families have no comparable narrow-code convention to
    filter on). Header text is truncated to 150 chars (matches indexer.py's
    own long-title truncation convention) as a guard against Docling
    mis-tagging a full paragraph as a header; not otherwise filtered.
    """
    groups: list[tuple[str | None, list[dict]]] = []
    current_header: str | None = None
    current_items: list[dict] = []
    for item in texts:
        label = item.get("label")
        if label in ("page_footer", "page_header"):
            continue
        if label == "section_header":
            if current_items:
                groups.append((current_header, current_items))
            text = item.get("text", "").strip()
            current_header = (text[:147] + "...") if len(text) > 150 else (text or None)
            current_items = [item]
            continue
        current_items.append(item)
    if current_items:
        groups.append((current_header, current_items))
    return groups


def build_chunks_for_file(content: dict, source_path: str, doc_family: str, norm_level, city, stem: str) -> tuple[list[Chunk], list[dict], dict]:
    texts = content.get("texts", [])
    groups = group_by_heading(texts)
    chunks: list[Chunk] = []
    drops: list[dict] = []
    report_sections: dict[str, dict] = {}
    idx = 0
    for header, items in groups:
        text = "\n".join(i["text"].strip() for i in items if i.get("text", "").strip())
        if not text:
            continue
        pages = [i["prov"][0]["page_no"] for i in items if i.get("prov")]
        page = min(pages) if pages else None
        page_end = max(pages) if pages else None
        pieces = [text] if len(text) <= CHUNK_SIZE else [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        for piece in pieces:
            ratio = _alpha_ratio(piece)
            if ratio < MIN_ALPHA_RATIO:
                drops.append({"type": "low_alpha", "source_path": source_path, "chunk_index": idx,
                               "alpha_ratio": round(ratio, 3), "section": header, "content_preview": piece[:80].replace("\n", " ")})
                idx += 1
                continue
            chunks.append(Chunk(
                chunk_id=f"{stem}-{idx}", source_path=source_path, doc_family=doc_family,
                content=piece, page=page, page_end=page_end, section=header,
                norm_level=norm_level, city=city, chunk_type=None,
            ))
            key = header if header is not None else "__no_header__"
            info = report_sections.setdefault(key, {"n_chunks": 0, "lengths": []})
            info["n_chunks"] += 1
            info["lengths"].append(len(piece))
            idx += 1
    return chunks, drops, report_sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage_name")
    ap.add_argument("families")  # comma-separated
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--exclude", default="")  # comma-separated filenames
    ap.add_argument("--only", default="")  # comma-separated filenames, restrict to these
    args = ap.parse_args()

    families = args.families.split(",")
    exclude = {f.strip() for f in args.exclude.split(",") if f.strip()}
    only = {f.strip() for f in args.only.split(",") if f.strip()}

    settings = Settings()
    rules = load_rules()
    from aria_rag.loader import iter_pdf_paths
    all_paths = iter_pdf_paths(settings.docs_dir)

    targets: list[tuple[Path, str, str | None, str | None]] = []
    for p in all_paths:
        c = classify_path(p, settings.docs_dir, rules)
        if c.family not in families or c.validity != "current":
            continue
        if p.name in exclude:
            continue
        if only and p.name not in only:
            continue
        targets.append((p, c.family, c.norm_level, c.city))

    print(f"=== Stage '{args.stage_name}': {len(targets)} file(s) across families {families} ===", flush=True)

    chunks_path = settings.index_dir / "chunks.json"
    manifest_path = settings.index_dir / "manifest.json"
    existing_raw = json.loads(chunks_path.read_text(encoding="utf-8"))
    existing_chunks = [Chunk(**item) for item in existing_raw]
    existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    new_chunks_all: list[Chunk] = []
    all_drops: list[dict] = []
    file_reports: dict[str, dict] = {}
    processed_targets: list[tuple[Path, str, str | None, str | None]] = []

    for p, family, norm_level, city in targets:
        stem = p.stem
        print(f"--- {family}/{p.name} ---", flush=True)
        status, content, msg = extract_one(p, family, args.timeout)
        print(f"    extract: {status} — {msg}", flush=True)
        if status not in ("ok", "cached"):
            file_reports[str(p)] = {"family": family, "status": status, "message": msg}
            continue
        source_path_on_disk = str(p)
        # Match existing chunk's stored source_path form (NFC-safe) if present, else this platform's str(p).
        target_nfc = unicodedata.normalize("NFC", source_path_on_disk)
        matches = [c for c in existing_chunks if unicodedata.normalize("NFC", c.source_path) == target_nfc]
        on_disk_source_path = matches[0].source_path if matches else source_path_on_disk
        before_count = len(matches)

        file_chunks, file_drops, report_sections = build_chunks_for_file(
            content, on_disk_source_path, family, norm_level, city, stem
        )
        for c in file_chunks:
            c.source_path = on_disk_source_path
        n_null = sum(1 for c in file_chunks if c.section is None)
        n_total = len(file_chunks)
        pct_null = round(n_null / n_total * 100, 1) if n_total else None
        print(f"    chunks: {n_total} kept ({n_null} null-section, {pct_null}%), {len(file_drops)} dropped (low-alpha), replacing {before_count} old", flush=True)
        file_reports[str(p)] = {
            "family": family, "status": status, "before_chunk_count": before_count,
            "new_chunk_count": n_total, "null_section_count": n_null, "pct_null_section": pct_null,
            "n_distinct_sections": len({s for s in report_sections if s != "__no_header__"}),
            "dropped_low_alpha": len(file_drops),
            "sample_sections": [s for s in list(report_sections.keys())[:15]],
        }
        new_chunks_all.extend(file_chunks)
        all_drops.extend(file_drops)
        processed_targets.append((p, family, norm_level, city))

    if not new_chunks_all:
        print("No chunks produced this stage (all files failed/timed out or produced nothing) — nothing to write.", flush=True)
        report_path = REPO_ROOT / "scratch" / f"section_report_{args.stage_name}.json"
        report_path.write_text(json.dumps(file_reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {report_path}", flush=True)
        return

    processed_nfc = {unicodedata.normalize("NFC", str(p)) for p, _, _, _ in processed_targets}
    kept_other_chunks = [c for c in existing_chunks if unicodedata.normalize("NFC", c.source_path) not in processed_nfc]
    all_chunks = kept_other_chunks + new_chunks_all

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
            dedup_ledger.append({"removed_chunk_id": c.chunk_id, "removed_source_path": c.source_path,
                                  "kept_chunk_id": kept.chunk_id, "kept_source_path": kept.source_path, "content_hash": h})
    all_chunks = unique_chunks
    all_chunks.sort(key=lambda c: (c.source_path, c.chunk_id))

    print(f"Building embeddings with {settings.embedding_model} for {len(all_chunks)} chunks (full re-embed)...", flush=True)
    model = SentenceTransformer(settings.embedding_model)
    texts_ = [c.content for c in all_chunks]
    embeddings = model.encode(texts_, batch_size=64, show_progress_bar=True, normalize_embeddings=True)
    embeddings = np.array(embeddings, dtype=np.float32)

    dimension = embeddings.shape[1]
    faiss_index = faiss.IndexFlatIP(dimension)
    faiss_index.add(embeddings)

    print("Building BM25 index...", flush=True)
    bm25 = BM25Okapi([_tokenize(c.content) for c in all_chunks])
    with open(settings.index_dir / "bm25.pkl", "wb") as f:
        pickle.dump(bm25, f)
    faiss.write_index(faiss_index, str(settings.index_dir / "index.faiss"))
    chunks_path.write_text(json.dumps([asdict(c) for c in all_chunks], ensure_ascii=False, indent=2), encoding="utf-8")

    new_manifest_entries = []
    for p, family, norm_level, city in processed_targets:
        size_bytes, modified_time = get_file_signature(p)
        n_this = sum(1 for c in new_chunks_all if unicodedata.normalize("NFC", c.source_path) == unicodedata.normalize("NFC", str(p)))
        new_manifest_entries.append({"source_path": str(p), "size_bytes": size_bytes, "modified_time": modified_time, "chunk_count": n_this})

    manifest_entries = [m for m in existing_manifest if unicodedata.normalize("NFC", m["source_path"]) not in processed_nfc] + new_manifest_entries
    manifest_entries.sort(key=lambda m: m["source_path"])
    manifest_path.write_text(json.dumps(manifest_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    (settings.index_dir / "drop_log.json").write_text(json.dumps(all_drops, ensure_ascii=False, indent=2), encoding="utf-8")
    (settings.index_dir / "dedup_ledger.json").write_text(json.dumps(dedup_ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    whitelist = build_article_whitelist(all_chunks)
    from aria_rag.indexer import ARTICLE_WHITELIST_PATH
    ARTICLE_WHITELIST_PATH.write_text(json.dumps(whitelist, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = REPO_ROOT / "scratch" / f"section_report_{args.stage_name}.json"
    report_path.write_text(json.dumps(file_reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {report_path}", flush=True)
    print(f"DONE — {len(processed_targets)}/{len(targets)} file(s) processed, {len(new_chunks_all)} new chunks, "
          f"{sum(1 for c in new_chunks_all if c.section is None)} null-section, total corpus now {len(all_chunks)} chunks", flush=True)


if __name__ == "__main__":
    main()
