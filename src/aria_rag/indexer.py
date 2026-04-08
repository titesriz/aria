from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Callable

from sklearn.feature_extraction.text import TfidfVectorizer

from aria_rag.config import Settings
from aria_rag.loader import iter_pdf_paths, read_pdf


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    source_path: str
    content: str


@dataclass(slots=True)
class IndexedFile:
    source_path: str
    size_bytes: int
    modified_time: float
    chunk_count: int


def create_vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(ngram_range=(1, 2), strip_accents="unicode")


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


def extract_chunks_from_pdf(
    path: Path, chunk_size: int, chunk_overlap: int
) -> list[Chunk]:
    document = read_pdf(path)
    if not document.text:
        return []

    return [
        Chunk(
            chunk_id=f"{Path(document.path).stem}-{idx}",
            source_path=document.path,
            content=chunk,
        )
        for idx, chunk in enumerate(chunk_text(document.text, chunk_size, chunk_overlap))
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
) -> tuple[int, int]:
    pdf_paths = iter_pdf_paths(settings.docs_dir)
    if settings.max_files is not None:
        pdf_paths = pdf_paths[: settings.max_files]

    existing_manifest = {} if rebuild else load_manifest(settings.index_dir)
    existing_chunks = {} if rebuild else load_existing_chunks(settings.index_dir)

    chunks: list[Chunk] = []
    manifest_entries: list[IndexedFile] = []
    paths_to_process: list[Path] = []
    total = len(pdf_paths)

    for index, path in enumerate(pdf_paths, start=1):
        source_path = str(path)
        size_bytes, modified_time = get_file_signature(path)
        cached = existing_manifest.get(source_path)
        cached_chunks = existing_chunks.get(source_path, [])
        if (
            cached is not None
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
            file_chunks = extract_chunks_from_pdf(path, settings.chunk_size, settings.chunk_overlap)
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
                    extract_chunks_from_pdf, path, settings.chunk_size, settings.chunk_overlap
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

    manifest_entries.sort(key=lambda item: item.source_path)
    chunks.sort(key=lambda chunk: (chunk.source_path, chunk.chunk_id))

    vectorizer = create_vectorizer()
    matrix = vectorizer.fit_transform(chunk.content for chunk in chunks)

    settings.index_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = settings.index_dir / "chunks.json"
    manifest_path = settings.index_dir / "manifest.json"
    vocab_path = settings.index_dir / "vocabulary.json"
    matrix_path = settings.index_dir / "matrix.npz"
    idf_path = settings.index_dir / "idf.npy"

    metadata_path.write_text(
        json.dumps([asdict(chunk) for chunk in chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps([asdict(item) for item in manifest_entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    vocab_path.write_text(
        json.dumps(vectorizer.vocabulary_, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    from scipy import sparse
    import numpy as np

    sparse.save_npz(matrix_path, matrix)
    np.save(idf_path, vectorizer.idf_)
    return len(pdf_paths), len(chunks)
