from __future__ import annotations

import bisect
import hashlib
import json
import logging
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

from aria_rag.config import ROOT_DIR, Settings
from aria_rag.loader import iter_pdf_paths, read_pdf

logger = logging.getLogger(__name__)

ARTICLE_WHITELIST_PATH = ROOT_DIR / "eval" / "article_whitelist.json"

DOC_FAMILIES = {
    "Règlement/Pièces écrites": "reglement_ecrit",
    "Règlement/Documents graphiques": "reglement_graphique",
    "Rapport de présentation": "rapport_presentation",
    "OAP": "oap",
    "PADD": "padd",
    "Annexes": "annexes",
}


def infer_doc_family(path: Path) -> str:
    path_str = unicodedata.normalize("NFC", path.as_posix())
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
    page: int | None = None
    page_end: int | None = None
    section: str | None = None


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
# Group 1 captures just the article code (e.g. "UG.3.1.1"), used for the Chunk.section field.
_ARTICLE_HEADER = re.compile(
    r'(?<!\()\b((?:UG(?:SU)?|UV|N|A|P)\w*\.\d+(?:\.\d+)*)\s+[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜŸÇ][a-zàâäéèêëîïôùûüÿç]'
)

# Detects table rows from PLU reservation lists (Annexe III, V…)
_TABLE_ROW = re.compile(r'\b(?:LS|BRS)\s+\d{2,3}-\d{2,3}\b')
# Detects address-list rows lacking an LS/BRS code (e.g. Annexe VI protected
# green spaces): "<arrondissement> <house number(s)> <street keyword> ...",
# one per line since loader.py (fix 2) preserves line breaks.
_ADDRESS_ROW = re.compile(
    r'(?m)^\s*\d{1,2}(?:er|e)?\s+.{0,40}?\b'
    r'(?:[Rr]ue|[Aa]venue|[Bb]oulevard|[Pp]lace|[Ii]mpasse|[Qq]uai|[Aa]llée|[Ss]quare|[Vv]illa|[Vv]oie|[Cc]ité|[Pp]assage)\b'
)
# Matches "ANNEXE V : LISTE…" headers as they appear in extracted PDF text
# "A NNEXE" (with space) is a common pypdf extraction artefact
#
# Corpus check (data/index/chunks.json): every genuine title header starts
# with an uppercase "A" — "ANNEXE X - LISTE..." (all-caps), "A NNEXE V :
# LISTE..." (the pypdf split artefact), or "Annexe V : Liste..." (Title
# Case, seen in REG2A1.pdf specifically). Every one of the 81 false-positive
# chunks (inline prose mentions like "l'annexe I du tome 2 du règlement
# écrit indique...") starts with a lowercase "a" — none of the 3,489 real
# headers do, and none of the 81 false positives don't. So only the leading
# "A" needs to be case-SENSITIVE; the rest of "NNEXE" stays scoped
# case-insensitive via (?i:...) to cover both the all-caps and Title Case
# variants. The roman numeral and the trailing title-starter char class are
# also left case-sensitive (uppercase-only), matching the original design
# intent (see _ARTICLE_HEADER's identical convention) and giving a second,
# independent signal — belt and suspenders. A bare `re.IGNORECASE` flag
# neutralized all of this, which is what let 81/4438 reglement_ecrit chunks
# pick up a garbage mid-sentence section value (see ingestion audit).
_ANNEXE_HEADER = re.compile(
    r'A\s*(?i:NNEXE)\s+[IVXLCDM]+\s*[:\–\-]?\s*[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜŸÇ][^\n]{0,120}'
)

# Matches a Chunk.section value that IS an article code (vs an annexe title) —
# same charset as _ARTICLE_HEADER's group 1, anchored to the whole string.
_ARTICLE_CODE_PATTERN = re.compile(r'^(?:UG(?:SU)?|UV|N|A|P)\w*\.\d+(?:\.\d+)*$')


def build_article_whitelist(chunks: list[Chunk]) -> list[str]:
    """Distinct article codes (Chunk.section values matching _ARTICLE_CODE_PATTERN),
    used by query_expansion.py to reject LLM-hallucinated codes like "DG.2.7".
    """
    codes = {
        c.section for c in chunks
        if c.section is not None and _ARTICLE_CODE_PATTERN.match(c.section)
    }
    return sorted(codes)


def _titled_annexe_matches(full_text: str) -> list[re.Match]:
    """ANNEXE header matches, excluding inline cross-references like "(Annexe IV)"."""
    return [
        m for m in _ANNEXE_HEADER.finditer(full_text)
        if not full_text[: m.start()].rstrip().endswith("(")
    ]


def _page_at_offset(page_starts: list[int], page_numbers: list[int], offset: int) -> int | None:
    """Real PDF page number containing the given character offset in full_text."""
    if not page_starts:
        return None
    i = bisect.bisect_right(page_starts, offset) - 1
    return page_numbers[i] if i >= 0 else None


def _strip_with_offset(text: str, base_offset: int) -> tuple[int, str] | None:
    """Strip whitespace, adjusting base_offset for any trimmed leading chars.

    Returns None if nothing remains after stripping.
    """
    stripped = text.strip()
    if not stripped:
        return None
    leading_ws = len(text) - len(text.lstrip())
    return base_offset + leading_ws, stripped


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[tuple[int, str]]:
    """Sliding-window split. Returns (start_offset, text) pairs — offset is
    this chunk's position in the original text, used for page attribution.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        result = _strip_with_offset(text[start:end], start)
        if result:
            chunks.append(result)
        if end >= len(text):
            break
        start = end - chunk_overlap
    return chunks


def _split_by_char(text: str, chunk_size: int, base_offset: int = 0) -> list[tuple[int, str]]:
    """Split text into fixed-size chunks by character (no overlap).

    base_offset shifts returned offsets into the coordinate space of the
    original full document text (text here is usually a substring, e.g. one
    oversized article).
    """
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        result = _strip_with_offset(text[start: start + chunk_size], base_offset + start)
        if result:
            chunks.append(result)
        start += chunk_size
    return chunks


def chunk_text_by_article(text: str, chunk_size: int, source_path: str = "") -> list[tuple[int, str]]:
    """Split text on PLU article headers (UG.X.X, UGSU.X, UV.X, N.X …).

    Each split starts at the header line. Articles longer than chunk_size are
    further split by character so no chunk blows up the embedding model.
    Falls back to a fixed-size character split when no headers are found.
    Returns (start_offset, text) pairs — offset is this chunk's position in
    the original text, used for page/section attribution.
    """
    boundaries = [m.start() for m in _ARTICLE_HEADER.finditer(text)]

    if not boundaries:
        logger.warning(
            "No article headers found in %s (%d chars) — falling back to fixed-size split",
            source_path, len(text),
        )
        result = _strip_with_offset(text, 0)
        if not result:
            return []
        leading_offset, stripped_text = result
        return _split_by_char(stripped_text, chunk_size, base_offset=leading_offset)

    # Add a sentinel at the end
    boundaries.append(len(text))

    raw_articles: list[tuple[int, str]] = []
    for i in range(len(boundaries) - 1):
        result = _strip_with_offset(text[boundaries[i]: boundaries[i + 1]], boundaries[i])
        if result:
            raw_articles.append(result)

    # Split oversized articles by character (no overlap — article boundary is the natural break)
    chunks: list[tuple[int, str]] = []
    for offset, article in raw_articles:
        if len(article) <= chunk_size:
            chunks.append((offset, article))
        else:
            chunks.extend(_split_by_char(article, chunk_size, base_offset=offset))
    return chunks


def extract_chunks_from_pdf(
    path: Path, chunk_size: int, chunk_overlap: int, min_alpha_ratio: float = 0.55
) -> tuple[list[Chunk], list[dict]]:
    """Returns (kept_chunks, drops). drops records what extraction discarded
    or degraded — low-alpha chunks and reglement_ecrit files that fell back
    to fixed-size chunking for lack of article headers — so build_index can
    persist it to drop_log.json instead of it only ever reaching a transient
    logger.warning (see the check suite's drop-log invariant).
    """
    drops: list[dict] = []
    try:
        document = read_pdf(path)
    except Exception as exc:
        logger.warning("Skipping %s — could not parse PDF: %s", path.name, exc)
        return [], drops
    if not document.pages:
        return [], drops

    doc_family = infer_doc_family(path)

    # Join pages into one string for the existing regex-based chunkers, while
    # tracking each page's start offset so chunks can be attributed back to
    # a real PDF page number — this is the "known at extraction time" data
    # the loader now provides, instead of counting \n after the fact.
    full_text_parts: list[str] = []
    page_starts: list[int] = []
    page_numbers: list[int] = []
    offset = 0
    for page_num, page_text in document.pages:
        page_starts.append(offset)
        page_numbers.append(page_num)
        full_text_parts.append(page_text)
        offset += len(page_text) + 1  # +1 for the "\n" joiner below
    full_text = "\n".join(full_text_parts)

    # Use article-aware chunking for regulatory documents; fall back to sliding window otherwise.
    if doc_family == "reglement_ecrit":
        raw_chunks = chunk_text_by_article(full_text, chunk_size, source_path=document.path)
        article_matches = list(_ARTICLE_HEADER.finditer(full_text))
        article_starts = [m.start() for m in article_matches]
        annexe_matches = _titled_annexe_matches(full_text)
        annexe_starts = [m.start() for m in annexe_matches]
        if not article_matches:
            # Same condition chunk_text_by_article checks internally (identical
            # regex over the identical full_text) to fall back to a fixed-size
            # split — detected here too, redundantly but harmlessly, purely to
            # log it without touching that function's own logic.
            drops.append({
                "type": "no_header_fallback",
                "source_path": document.path,
                "text_length": len(full_text),
            })
    else:
        raw_chunks = chunk_text(full_text, chunk_size, chunk_overlap)
        article_matches = article_starts = annexe_matches = annexe_starts = []

    kept: list[Chunk] = []
    for idx, (start_offset, chunk) in enumerate(raw_chunks):
        page = _page_at_offset(page_starts, page_numbers, start_offset)
        # End offset of the chunk's own text, computed before any section-prefix
        # is prepended below — the prefix is synthesized metadata, not part of
        # the document, so it must not shift which page counts as the last one.
        end_offset = start_offset + max(len(chunk) - 1, 0)
        page_end = _page_at_offset(page_starts, page_numbers, end_offset)

        section: str | None = None
        if doc_family == "reglement_ecrit":
            # Pick whichever structural header (article or annexe) most recently
            # precedes this chunk. Tome 1 zone text only has article headers;
            # Tome 2 annexe text only has annexe headers, so this naturally
            # picks the right kind for each document without extra branching.
            ai = bisect.bisect_right(article_starts, start_offset) - 1
            ni = bisect.bisect_right(annexe_starts, start_offset) - 1
            article_pos = article_starts[ai] if ai >= 0 else -1
            annexe_pos = annexe_starts[ni] if ni >= 0 else -1
            if annexe_pos > article_pos:
                section = annexe_matches[ni].group(0).strip()
                if len(section) > 100:
                    section = section[:97] + '...'
                # Keep a minimal one-line context prefix — annexe titles carry
                # real BM25 signal (e.g. queries mentioning "annexe V") that
                # the table-row content itself doesn't contain.
                chunk = f"[Section: {section}]\n{chunk}"
            elif article_pos >= 0:
                section = article_matches[ai].group(1)

        ratio = _alpha_ratio(chunk)
        # Number-heavy table/address rows (LS/BRS codes, arrondissement address
        # lists) are legitimate low-alpha content — don't drop those.
        if ratio < min_alpha_ratio and not (_TABLE_ROW.search(chunk) or _ADDRESS_ROW.search(chunk)):
            logger.warning(
                "Dropping low-alpha chunk from %s (alpha=%.3f): %s",
                path.name, ratio, chunk[:80].replace("\n", " "),
            )
            drops.append({
                "type": "low_alpha",
                "source_path": document.path,
                "chunk_index": idx,
                "alpha_ratio": round(ratio, 3),
                "content_preview": chunk[:80].replace("\n", " "),
            })
            continue
        kept.append(
            Chunk(
                chunk_id=f"{Path(document.path).stem}-{idx}",
                source_path=document.path,
                doc_family=doc_family,
                content=chunk,
                page=page,
                page_end=page_end,
                section=section,
            )
        )
    return kept, drops


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
    all_drops: list[dict] = []
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
            file_chunks, file_drops = extract_chunks_from_pdf(path, settings.chunk_size, settings.chunk_overlap, settings.min_alpha_ratio)
            chunks.extend(file_chunks)
            all_drops.extend(file_drops)
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
                    file_chunks, file_drops = future.result()
                    completed += 1
                    chunks.extend(file_chunks)
                    all_drops.extend(file_drops)
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
    seen_hashes: dict[str, Chunk] = {}
    unique_chunks: list[Chunk] = []
    dedup_ledger: list[dict] = []
    for chunk in chunks:
        h = hashlib.md5(chunk.content.encode()).hexdigest()
        kept_chunk = seen_hashes.get(h)
        if kept_chunk is None:
            seen_hashes[h] = chunk
            unique_chunks.append(chunk)
        else:
            dedup_ledger.append({
                "removed_chunk_id": chunk.chunk_id,
                "removed_source_path": chunk.source_path,
                "kept_chunk_id": kept_chunk.chunk_id,
                "kept_source_path": kept_chunk.source_path,
                "content_hash": h,
            })
    duplicates_removed = len(dedup_ledger)
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
    # Audit trail for the check suite (aria_rag.check) — previously these only
    # ever reached a transient logger.warning. Reflects only files reprocessed
    # this run (empty on a no-op incremental ingest), not a historical archive.
    (settings.index_dir / "drop_log.json").write_text(
        json.dumps(all_drops, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (settings.index_dir / "dedup_ledger.json").write_text(
        json.dumps(dedup_ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    whitelist = build_article_whitelist(chunks)
    ARTICLE_WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTICLE_WHITELIST_PATH.write_text(
        json.dumps(whitelist, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(whitelist)} article codes to {ARTICLE_WHITELIST_PATH}", flush=True)

    return len(pdf_paths), len(chunks)
