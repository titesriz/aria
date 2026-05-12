from __future__ import annotations

import hashlib
import json
import pickle
import re
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Callable

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from aria_rag.config import Settings
from aria_rag.loader import iter_pdf_paths, read_pdf


DOC_FAMILIES = {
    "Règlement/Pièces écrites": "reglement_ecrit",
    "Règlement/Documents graphiques": "reglement_graphique",
    "Rapport de présentation": "rapport_presentation",
    "OAP": "oap",
    "PADD": "padd",
    "Annexes": "annexes",
}


def infer_doc_family(path: Path) -> str:
    path_str = unicodedata.normalize("NFC", str(path))
    for fragment, family in DOC_FAMILIES.items():
        if fragment in path_str:
            return family
    return "other"


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    source_path: str
    doc_family: str
    content: str


@dataclass(slots=True)
class IndexedFile:
    source_path: str
    size_bytes: int
    modified_time: float
    chunk_count: int


def _tokenize(text: str) -> list[str]:
    return re.findall(r'\b\w+\b', text.lower())


def _alpha_ratio(text: str) -> float:
    return sum(1 for c in text if c.isalpha()) / len(text) if text else 0.0


# Matches PLU article headers like "UG.3.1.1 Implantation..." in running text.
# Excludes inline cross-references like "(UG.3.1.1, 3°)" by requiring:
#   - not preceded by "("
#   - followed by a capitalized French word (the article title), not a comma or another code
_ARTICLE_HEADER = re.compile(
    r'(?<!\()\b(UG(?:SU)?|UV|N|A|P)\w*\.\d+(?:\.\d+)*\s+[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜŸÇ][a-zàâäéèêëîïôùûüÿç]'
)


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - chunk_overlap
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def chunk_text_by_article(text: str, chunk_size: int) -> list[str]:
    """Split text on PLU article headers (UG.X.X, UGSU.X, UV.X, N.X …).

    Each split starts at the header line. Articles longer than chunk_size are
    further split by character so no chunk blows up the embedding model.
    Falls back to the full text as a single chunk when no headers are found.
    """
    boundaries = [m.start() for m in _ARTICLE_HEADER.finditer(text)]

    if not boundaries:
        # No article structure detected — use the whole text as one block
        return [text.strip()] if text.strip() else []

    # Add a sentinel at the end
    boundaries.append(len(text))

    raw_articles: list[str] = []
    for i in range(len(boundaries) - 1):
        article = text[boundaries[i]: boundaries[i + 1]].strip()
        if article:
            raw_articles.append(article)

    # Split oversized articles by character (no overlap — article boundary is the natural break)
    chunks: list[str] = []
    for article in raw_articles:
        if len(article) <= chunk_size:
            chunks.append(article)
        else:
            start = 0
            while start < len(article):
                chunks.append(article[start: start + chunk_size].strip())
                start += chunk_size
    return [c for c in chunks if c]


def extract_chunks_from_pdf(
    path: Path, chunk_size: int, chunk_overlap: int, min_alpha_ratio: float = 0.55
) -> list[Chunk]:
    document = read_pdf(path)
    if not document.text:
        return []

    doc_family = infer_doc_family(path)

    # Use article-aware chunking for regulatory documents; fall back to sliding window otherwise.
    if doc_family == "reglement_ecrit":
        raw_chunks = chunk_text_by_article(document.text, chunk_size)
    else:
        raw_chunks = chunk_text(document.text, chunk_size, chunk_overlap)

    return [
        Chunk(
            chunk_id=f"{Path(document.path).stem}-{idx}",
            source_path=document.path,
            doc_family=doc_family,
            content=chunk,
        )
        for idx, chunk in enumerate(raw_chunks)
        if _alpha_ratio(chunk) >= min_alpha_ratio
    ]


def get_file_signature(path: Path) -> tuple[int, float]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime


def load_existing_chunks(index_dir: Path) -> dict[str, list[Chunk]]:
    metadata_path = index_dir / "chunks.json"
    if not metadata_path.exists():
        return {}

    chunks_by_source: dict[str, list[Chunk]] = {}
    for item in json.loads(metadata_path.read_text(encoding="utf-8")):
        chunk = Chunk(**item)
        chunks_by_source.setdefault(chunk.source_path, []).append(chunk)
    return chunks_by_source


def load_manifest(index_dir: Path) -> dict[str, IndexedFile]:
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {item["source_path"]: IndexedFile(**item) for item in data}


def build_index(
    settings: Settings,
    workers: int = 1,
    progress_callback: Callable[[int, int, Path, int, str], None] | None = None,
    heartbeat_callback: Callable[[int, int, int], None] | None = None,
    rebuild: bool = False,
    family_filter: list[str] | None = None,
) -> tuple[int, int]:
    pdf_paths = iter_pdf_paths(settings.docs_dir)
    if settings.max_files is not None:
        pdf_paths = pdf_paths[: settings.max_files]

    # With a family filter, always load existing data — files outside the filter are kept as-is.
    force_rebuild_all = rebuild and not family_filter
    existing_manifest = {} if force_rebuild_all else load_manifest(settings.index_dir)
    existing_chunks = {} if force_rebuild_all else load_existing_chunks(settings.index_dir)

    chunks: list[Chunk] = []
    manifest_entries: list[IndexedFile] = []
    paths_to_process: list[Path] = []
    total = len(pdf_paths)

    for index, path in enumerate(pdf_paths, start=1):
        source_path = str(path)
        size_bytes, modified_time = get_file_signature(path)
        cached = existing_manifest.get(source_path)
        cached_chunks = existing_chunks.get(source_path, [])

        # Force reprocess if: no family filter and rebuild=True,
        # OR family filter matches this file and rebuild=True.
        force_this_file = rebuild and (
            not family_filter or infer_doc_family(path) in family_filter
        )

        if (
            not force_this_file
            and cached is not None
            and cached.size_bytes == size_bytes
            and cached.modified_time == modified_time
            and len(cached_chunks) == cached.chunk_count
        ):
            chunks.extend(cached_chunks)
            manifest_entries.append(cached)
            if progress_callback is not None:
                progress_callback(index, total, path, len(cached_chunks), "cached")
            continue
        paths_to_process.append(path)

    processed_so_far = total - len(paths_to_process)
    if workers <= 1:
        for offset, path in enumerate(paths_to_process, start=1):
            file_chunks = extract_chunks_from_pdf(path, settings.chunk_size, settings.chunk_overlap, settings.min_alpha_ratio)
            chunks.extend(file_chunks)
            size_bytes, modified_time = get_file_signature(path)
            manifest_entries.append(
                IndexedFile(
                    source_path=str(path),
                    size_bytes=size_bytes,
                    modified_time=modified_time,
                    chunk_count=len(file_chunks),
                )
            )
            if progress_callback is not None:
                progress_callback(
                    processed_so_far + offset,
                    total,
                    path,
                    len(file_chunks),
                    "processed",
                )
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            future_to_path = {
                executor.submit(
                    extract_chunks_from_pdf, path, settings.chunk_size, settings.chunk_overlap, settings.min_alpha_ratio
                ): path
                for path in paths_to_process
            }
            completed = processed_so_far
            pending = set(future_to_path)
            last_heartbeat = monotonic()

            while pending:
                done, pending = wait(pending, timeout=15, return_when=FIRST_COMPLETED)
                if not done:
                    if heartbeat_callback is not None:
                        heartbeat_callback(completed, total, len(pending))
                    last_heartbeat = monotonic()
                    continue

                for future in done:
                    path = future_to_path[future]
                    file_chunks = future.result()
                    completed += 1
                    chunks.extend(file_chunks)
                    size_bytes, modified_time = get_file_signature(path)
                    manifest_entries.append(
                        IndexedFile(
                            source_path=str(path),
                            size_bytes=size_bytes,
                            modified_time=modified_time,
                            chunk_count=len(file_chunks),
                        )
                    )
                    if progress_callback is not None:
                        progress_callback(
                            completed,
                            total,
                            path,
                            len(file_chunks),
                            "processed",
                        )
                if pending and monotonic() - last_heartbeat >= 15 and heartbeat_callback is not None:
                    heartbeat_callback(completed, total, len(pending))
                    last_heartbeat = monotonic()

    if not chunks:
        raise RuntimeError(f"No text extracted from PDFs in {settings.docs_dir}")

    # Deduplicate chunks with identical content (e.g. legend files duplicated across atlas directories)
    seen_hashes: set[str] = set()
    unique_chunks: list[Chunk] = []
    for chunk in chunks:
        h = hashlib.md5(chunk.content.encode()).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique_chunks.append(chunk)
    duplicates_removed = len(chunks) - len(unique_chunks)
    if duplicates_removed:
        print(f"Removed {duplicates_removed} duplicate chunks.", flush=True)
    chunks = unique_chunks

    manifest_entries.sort(key=lambda item: item.source_path)
    chunks.sort(key=lambda chunk: (chunk.source_path, chunk.chunk_id))

    print(f"Building embeddings with {settings.embedding_model} for {len(chunks)} chunks...", flush=True)
    model = SentenceTransformer(settings.embedding_model)
    texts = [chunk.content for chunk in chunks]
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True)
    embeddings = np.array(embeddings, dtype=np.float32)

    dimension = embeddings.shape[1]
    faiss_index = faiss.IndexFlatIP(dimension)  # inner product = cosine similarity (normalized vectors)
    faiss_index.add(embeddings)

    settings.index_dir.mkdir(parents=True, exist_ok=True)

    print(f"Building BM25 index...", flush=True)
    bm25 = BM25Okapi([_tokenize(chunk.content) for chunk in chunks])
    with open(settings.index_dir / "bm25.pkl", "wb") as f:
        pickle.dump(bm25, f)

    faiss.write_index(faiss_index, str(settings.index_dir / "index.faiss"))
    (settings.index_dir / "chunks.json").write_text(
        json.dumps([asdict(chunk) for chunk in chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (settings.index_dir / "manifest.json").write_text(
        json.dumps([asdict(item) for item in manifest_entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return len(pdf_paths), len(chunks)
