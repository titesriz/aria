from __future__ import annotations

import json
import logging
import pickle
import re
from dataclasses import dataclass

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from aria_rag.config import Settings
from aria_rag.indexer import Chunk

logger = logging.getLogger(__name__)

RRF_K = 60


@dataclass(slots=True)
class SearchHit:
    source_path: str
    doc_family: str
    score: float
    content: str


def _tokenize(text: str) -> list[str]:
    return re.findall(r'\b\w+\b', text.lower())


def load_index(settings: Settings) -> tuple[SentenceTransformer, faiss.Index, object, list[Chunk]]:
    index_path = settings.index_dir / "index.faiss"
    chunks_path = settings.index_dir / "chunks.json"
    bm25_path = settings.index_dir / "bm25.pkl"

    if not index_path.exists():
        raise RuntimeError(
            f"Index not found in {settings.index_dir}. Run `aria-rag ingest` first."
        )

    faiss_index = faiss.read_index(str(index_path))
    chunks = [Chunk(**item) for item in json.loads(chunks_path.read_text(encoding="utf-8"))]
    with open(bm25_path, "rb") as f:
        bm25 = pickle.load(f)
    model = SentenceTransformer(settings.embedding_model)
    return model, faiss_index, bm25, chunks


def _rrf_scores(
    query: str,
    model: SentenceTransformer,
    faiss_index: faiss.Index,
    bm25: object,
    chunks: list[Chunk],
    allowed: set[int],
    fetch_k: int,
) -> dict[int, float]:
    """Compute RRF scores for a query over the allowed chunk set."""
    query_embedding = model.encode([query], normalize_embeddings=True)
    query_embedding = np.array(query_embedding, dtype=np.float32)
    _, sem_indices_raw = faiss_index.search(query_embedding, fetch_k)
    sem_indices = [int(i) for i in sem_indices_raw[0] if i != -1 and i in allowed]

    bm25_scores = bm25.get_scores(_tokenize(query))
    bm25_all = list(np.argsort(bm25_scores)[::-1])
    bm25_indices = [i for i in bm25_all if i in allowed][:fetch_k]

    rrf: dict[int, float] = {}
    for rank, idx in enumerate(sem_indices):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, idx in enumerate(bm25_indices):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
    return rrf


def search(
    settings: Settings,
    query: str,
    top_k: int | None = None,
    family_filter: list[str] | None = None,
) -> list[SearchHit]:
    model, faiss_index, bm25, chunks = load_index(settings)
    limit = top_k or settings.top_k

    if family_filter:
        allowed = {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
    else:
        allowed = set(range(len(chunks)))

    if not allowed:
        return []

    fetch_k = min(limit * 10, len(allowed))
    rrf = _rrf_scores(query, model, faiss_index, bm25, chunks, allowed, fetch_k)
    top_indices = sorted(rrf, key=rrf.__getitem__, reverse=True)[:limit]
    return [
        SearchHit(
            source_path=chunks[i].source_path,
            doc_family=chunks[i].doc_family,
            score=rrf[i],
            content=chunks[i].content,
        )
        for i in top_indices
    ]


def search_weighted(
    settings: Settings,
    query_original: str,
    query_expansion: str | None,
    top_k: int | None = None,
    alpha: float = 0.7,
    family_filter: list[str] | None = None,
) -> list[SearchHit]:
    """Hybrid weighted retrieval: alpha * RRF(original) + (1-alpha) * RRF(expansion).

    Falls back to search(query_original) if query_expansion is empty or if the
    expansion retrieval raises an exception.
    """
    if not query_expansion or not query_expansion.strip():
        return search(settings, query_original, top_k=top_k, family_filter=family_filter)

    logger.debug("search_weighted: alpha=%.2f, expansion=%r", alpha, query_expansion)

    model, faiss_index, bm25, chunks = load_index(settings)
    limit = top_k or settings.top_k

    if family_filter:
        allowed = {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
    else:
        allowed = set(range(len(chunks)))

    if not allowed:
        return []

    fetch_k = min(limit * 10, len(allowed))

    rrf_orig = _rrf_scores(query_original, model, faiss_index, bm25, chunks, allowed, fetch_k)

    try:
        rrf_exp = _rrf_scores(query_expansion, model, faiss_index, bm25, chunks, allowed, fetch_k)
    except Exception as exc:
        logger.warning("search_weighted: expansion retrieval failed (%s), falling back to original", exc)
        top_indices = sorted(rrf_orig, key=rrf_orig.__getitem__, reverse=True)[:limit]
        return [
            SearchHit(
                source_path=chunks[i].source_path,
                doc_family=chunks[i].doc_family,
                score=rrf_orig[i],
                content=chunks[i].content,
            )
            for i in top_indices
        ]

    all_ids = set(rrf_orig) | set(rrf_exp)
    combined: dict[int, float] = {
        i: alpha * rrf_orig.get(i, 0.0) + (1 - alpha) * rrf_exp.get(i, 0.0)
        for i in all_ids
    }
    top_indices = sorted(combined, key=combined.__getitem__, reverse=True)[:limit]
    return [
        SearchHit(
            source_path=chunks[i].source_path,
            doc_family=chunks[i].doc_family,
            score=combined[i],
            content=chunks[i].content,
        )
        for i in top_indices
    ]
