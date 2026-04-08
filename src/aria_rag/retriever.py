from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
from scipy import sparse

from aria_rag.config import Settings
from aria_rag.indexer import Chunk, create_vectorizer


@dataclass(slots=True)
class SearchHit:
    source_path: str
    score: float
    content: str


def load_index(settings: Settings) -> tuple[object, sparse.csr_matrix, list[Chunk]]:
    metadata_path = settings.index_dir / "chunks.json"
    vocab_path = settings.index_dir / "vocabulary.json"
    matrix_path = settings.index_dir / "matrix.npz"
    idf_path = settings.index_dir / "idf.npy"

    if not metadata_path.exists():
        raise RuntimeError(
            f"Index not found in {settings.index_dir}. Run `aria-rag ingest` first."
        )

    chunks = [Chunk(**item) for item in json.loads(metadata_path.read_text(encoding="utf-8"))]
    vocabulary = json.loads(vocab_path.read_text(encoding="utf-8"))
    matrix = sparse.load_npz(matrix_path)
    idf = np.load(idf_path)

    vectorizer = create_vectorizer()
    vectorizer.set_params(vocabulary=vocabulary)
    vectorizer.idf_ = idf
    vectorizer._tfidf._idf_diag = sparse.spdiags(
        idf,
        diags=0,
        m=len(idf),
        n=len(idf),
    )
    return vectorizer, matrix, chunks


def search(settings: Settings, query: str, top_k: int | None = None) -> list[SearchHit]:
    vectorizer, matrix, chunks = load_index(settings)
    query_vector = vectorizer.transform([query])
    scores = (matrix @ query_vector.T).toarray().ravel()
    limit = top_k or settings.top_k
    best_indices = np.argsort(scores)[::-1][:limit]

    hits: list[SearchHit] = []
    for idx in best_indices:
        score = float(scores[idx])
        if score <= 0:
            continue
        chunk = chunks[idx]
        hits.append(SearchHit(source_path=chunk.source_path, score=score, content=chunk.content))
    return hits
