from __future__ import annotations

import json
import logging
import pickle
import re
from dataclasses import dataclass
from typing import Callable

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from aria_rag.config import Settings
from aria_rag.indexer import Chunk

logger = logging.getLogger(__name__)

RRF_K = 60

# French ordinal words for arrondissement numbers, e.g. "premier arrondissement".
# Paris only goes to 20, so this list is exhaustive.
_ARRONDISSEMENT_WORDS = {
    "premier": 1, "deuxieme": 2, "deuxième": 2, "troisieme": 3, "troisième": 3,
    "quatrieme": 4, "quatrième": 4, "cinquieme": 5, "cinquième": 5,
    "sixieme": 6, "sixième": 6, "septieme": 7, "septième": 7,
    "huitieme": 8, "huitième": 8, "neuvieme": 9, "neuvième": 9,
    "dixieme": 10, "dixième": 10, "onzieme": 11, "onzième": 11,
    "douzieme": 12, "douzième": 12, "treizieme": 13, "treizième": 13,
    "quatorzieme": 14, "quatorzième": 14, "quinzieme": 15, "quinzième": 15,
    "seizieme": 16, "seizième": 16, "dix-septieme": 17, "dix-septième": 17,
    "dix-huitieme": 18, "dix-huitième": 18, "dix-neuvieme": 19, "dix-neuvième": 19,
    "vingtieme": 20, "vingtième": 20,
}
# "11e arrondissement", "1er arrondissement", "8ème arrondissement"
_ARRONDISSEMENT_NUMERIC = re.compile(r'\b(\d{1,2})(?:er|e|ème|eme)\s+arrondissement\b', re.IGNORECASE)
_ARRONDISSEMENT_WORD = re.compile(
    r'\b(' + '|'.join(_ARRONDISSEMENT_WORDS) + r')\s+arrondissement\b', re.IGNORECASE
)
# Article codes literally mentioned in a query (e.g. an expansion query like "UG.3.1.1")
_ARTICLE_CODE_IN_QUERY = re.compile(r'\b(?:UG(?:SU)?|UV|N|A|P)\w*\.\d+(?:\.\d+)*\b')


def _arrondissement_token(n: int) -> str:
    """Canonical shorthand as it appears in extracted PLU table rows."""
    return "1er" if n == 1 else f"{n}e"


def extract_discriminating_tokens(query_text: str) -> list[tuple[str, str]]:
    """Tokens worth an exact-match lexical boost, as (token, kind) pairs.

    kind="arrondissement": shorthand like "1er"/"15e". These are ordinary
    French words in other contexts ("1er janvier", "1er alinéa", "article
    1er de la loi") — matching them bare anywhere in a chunk over-triggers,
    so callers must anchor to line-start (how address-table rows actually
    format: "1er 15 rue d'Argenteuil...").

    kind="article_code": codes like "UG.3.1.1" mentioned in the query
    (typically from an expansion query). Not ordinary words, safe to match
    anywhere in the text.
    """
    tokens: set[tuple[str, str]] = set()
    for m in _ARRONDISSEMENT_NUMERIC.finditer(query_text):
        tokens.add((_arrondissement_token(int(m.group(1))), "arrondissement"))
    for m in _ARRONDISSEMENT_WORD.finditer(query_text.lower()):
        tokens.add((_arrondissement_token(_ARRONDISSEMENT_WORDS[m.group(1)]), "arrondissement"))
    for m in _ARTICLE_CODE_IN_QUERY.finditer(query_text):
        tokens.add((m.group(0), "article_code"))
    return sorted(tokens)


def _apply_lexical_boost(
    query_text: str,
    scores: dict[int, float],
    chunks: list[Chunk],
    boost_factor: float,
) -> dict[int, float]:
    """Multiply the score of any candidate whose content or section contains
    an exact discriminating token from the query. No-op (returns scores
    unchanged) when the query has no such tokens or boost_factor == 1.0 —
    generic queries are completely unaffected.
    """
    if boost_factor == 1.0:
        return scores
    tokens = extract_discriminating_tokens(query_text)
    if not tokens:
        return scores

    patterns = []
    for tok, kind in tokens:
        if kind == "arrondissement":
            # Line-start only — this is how table rows format ("1er 15 rue
            # d'Argenteuil..."); a bare match would also hit "1er janvier",
            # "article 1er de la loi", etc.
            patterns.append(re.compile(rf'(?m)^\s*{re.escape(tok)}\b'))
        else:
            patterns.append(re.compile(rf'\b{re.escape(tok)}\b'))

    boosted = dict(scores)
    for idx, score in scores.items():
        chunk = chunks[idx]
        haystack = chunk.content if not chunk.section else f"{chunk.content} {chunk.section}"
        if any(p.search(haystack) for p in patterns):
            boosted[idx] = score * boost_factor
    return boosted


@dataclass(slots=True)
class SearchHit:
    source_path: str
    doc_family: str
    score: float
    content: str
    page: int | None = None
    section: str | None = None
    faiss_score: float | None = None
    bm25_score: float | None = None


def scoped_retrieval_merge(
    scopes: list[str],
    slots: dict[str, int],
    total_k: int,
    fetch_fn: Callable[[str, int], list[SearchHit]],
) -> list[SearchHit]:
    """Run retrieval separately per scope value, then merge into one list.

    General-purpose: scope is whatever dimension the caller wants to search
    independently before merging — document family today (the crowd-out
    problem: one global top-k across a large corpus lets the biggest family
    win by sheer volume), norm_level (PLU vs CCH) later. Swap in a different
    `scopes`/`slots`/`fetch_fn` and the merge logic is unchanged.

    fetch_fn(scope, k) must return up to k hits for that scope alone, best
    first, from whatever underlying retrieval + boost pipeline the caller
    uses — this function only does allocation and merge order, not scoring.

    Allocation: each scope gets `slots[scope]` hits. A scope with fewer
    results than its allocation frees up slots, which go to whichever
    scope (any of them) has the strongest next unselected candidate —
    repeated until either the budget (total_k) is filled or every scope's
    fetched pool is exhausted.

    Merge order: per-scope rank tier first (every scope's own #1 hit comes
    before any scope's #2), sorted by score within each tier. This keeps
    the result diverse by construction rather than by chance — a single
    dominant scope can't push a weaker scope's best hit off the list just
    because its 4th-best result scores higher than that scope's 1st-best.
    """
    per_scope_hits: dict[str, list[SearchHit]] = {}
    for scope in scopes:
        n_slots = slots.get(scope, 0)
        if n_slots <= 0:
            continue
        hits = fetch_fn(scope, total_k)
        if hits:
            per_scope_hits[scope] = hits

    selected: dict[str, list[SearchHit]] = {}
    leftover_pool: list[tuple[str, SearchHit]] = []
    used_slots = 0
    for scope, hits in per_scope_hits.items():
        n_slots = slots.get(scope, 0)
        selected[scope] = hits[:n_slots]
        used_slots += len(selected[scope])
        leftover_pool.extend((scope, h) for h in hits[n_slots:])

    unused_slots = total_k - used_slots
    leftover_pool.sort(key=lambda pair: pair[1].score, reverse=True)
    for scope, hit in leftover_pool:
        if unused_slots <= 0:
            break
        selected.setdefault(scope, []).append(hit)
        unused_slots -= 1

    max_rank = max((len(hits) for hits in selected.values()), default=0)
    merged: list[SearchHit] = []
    for rank in range(max_rank):
        tier = [hits[rank] for hits in selected.values() if rank < len(hits)]
        tier.sort(key=lambda h: h.score, reverse=True)
        merged.extend(tier)
    return merged[:total_k]


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


def _compute_all_scores(
    query: str,
    model: SentenceTransformer,
    faiss_index: faiss.Index,
    bm25: object,
    chunks: list[Chunk],
    allowed: set[int],
    fetch_k: int,
) -> tuple[dict[int, float], dict[int, float], dict[int, float]]:
    """Return (rrf_scores, faiss_scores, bm25_scores) dicts keyed by chunk index."""
    query_embedding = model.encode([query], normalize_embeddings=True)
    query_embedding = np.array(query_embedding, dtype=np.float32)
    raw_scores, sem_indices_raw = faiss_index.search(query_embedding, fetch_k)

    faiss_scores: dict[int, float] = {}
    sem_indices: list[int] = []
    for score, idx in zip(raw_scores[0], sem_indices_raw[0]):
        idx = int(idx)
        if idx != -1 and idx in allowed:
            faiss_scores[idx] = float(score)
            sem_indices.append(idx)

    bm25_raw = bm25.get_scores(_tokenize(query))
    bm25_scores: dict[int, float] = {i: float(bm25_raw[i]) for i in allowed}
    bm25_all = list(np.argsort(bm25_raw)[::-1])
    bm25_indices = [i for i in bm25_all if i in allowed][:fetch_k]

    rrf: dict[int, float] = {}
    for rank, idx in enumerate(sem_indices):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, idx in enumerate(bm25_indices):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)

    return rrf, faiss_scores, bm25_scores


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
    rrf, _, _ = _compute_all_scores(query, model, faiss_index, bm25, chunks, allowed, fetch_k)
    return rrf


def _search_within(
    model: SentenceTransformer,
    faiss_index: faiss.Index,
    bm25: object,
    chunks: list[Chunk],
    query: str,
    limit: int,
    allowed: set[int],
    boost_factor: float = 1.0,
    debug: bool = False,
) -> list[SearchHit]:
    """Core single-pool RRF search over `allowed`, with optional lexical
    boost. Used both for the traditional single-pool path (no family filter,
    or scoped retrieval disabled) and as the per-scope fetch inside
    scoped_retrieval_merge.
    """
    if not allowed:
        return []

    # A discriminating token (arrondissement number, article code) can rank
    # respectably on BM25 alone while still sitting far outside the default
    # top_k*10 candidate window — widen the fetch so the boost below has a
    # candidate to actually promote, instead of boosting an empty set.
    has_tokens = bool(extract_discriminating_tokens(query))
    fetch_k = len(allowed) if has_tokens else min(limit * 10, len(allowed))

    if debug:
        rrf, faiss_scores, bm25_scores = _compute_all_scores(
            query, model, faiss_index, bm25, chunks, allowed, fetch_k
        )
    else:
        rrf = _rrf_scores(query, model, faiss_index, bm25, chunks, allowed, fetch_k)
        faiss_scores = bm25_scores = {}

    rrf = _apply_lexical_boost(query, rrf, chunks, boost_factor)

    top_indices = sorted(rrf, key=rrf.__getitem__, reverse=True)[:limit]
    return [
        SearchHit(
            source_path=chunks[i].source_path,
            doc_family=chunks[i].doc_family,
            score=rrf[i],
            content=chunks[i].content,
            page=chunks[i].page,
            section=chunks[i].section,
            faiss_score=faiss_scores.get(i) if debug else None,
            bm25_score=bm25_scores.get(i) if debug else None,
        )
        for i in top_indices
    ]


def search(
    settings: Settings,
    query: str,
    top_k: int | None = None,
    family_filter: list[str] | None = None,
    debug: bool = False,
    scoped: bool | None = None,
) -> list[SearchHit]:
    """Retrieve the top-k chunks for `query`.

    family_filter, if given, restricts the search to those families only —
    a plain single-pool search, unchanged from before scoped retrieval
    existed. Without it, and when scoped retrieval is enabled (the default,
    settings.scoped_retrieval — override per-call with `scoped`), the
    search runs separately per family (settings.family_slots) and merges,
    so one large family can't crowd out the others in the final top-k.
    """
    model, faiss_index, bm25, chunks = load_index(settings)
    limit = top_k or settings.top_k
    use_scoped = settings.scoped_retrieval if scoped is None else scoped

    if family_filter:
        allowed = {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
        return _search_within(
            model, faiss_index, bm25, chunks, query, limit, allowed,
            boost_factor=settings.lexical_boost_factor, debug=debug,
        )

    if not use_scoped:
        allowed = set(range(len(chunks)))
        return _search_within(
            model, faiss_index, bm25, chunks, query, limit, allowed,
            boost_factor=settings.lexical_boost_factor, debug=debug,
        )

    present_families = {c.doc_family for c in chunks}
    scopes = [f for f in settings.family_slots if f in present_families]
    if not scopes:
        allowed = set(range(len(chunks)))
        return _search_within(
            model, faiss_index, bm25, chunks, query, limit, allowed,
            boost_factor=settings.lexical_boost_factor, debug=debug,
        )

    def fetch_fn(family: str, k: int) -> list[SearchHit]:
        fam_allowed = {i for i, c in enumerate(chunks) if c.doc_family == family}
        return _search_within(
            model, faiss_index, bm25, chunks, query, k, fam_allowed,
            boost_factor=settings.lexical_boost_factor, debug=debug,
        )

    return scoped_retrieval_merge(scopes, settings.family_slots, limit, fetch_fn)


def _search_weighted_within(
    model: SentenceTransformer,
    faiss_index: faiss.Index,
    bm25: object,
    chunks: list[Chunk],
    query_original: str,
    query_expansion: str | None,
    limit: int,
    allowed: set[int],
    alpha: float,
    boost_factor: float = 1.0,
) -> list[SearchHit]:
    """Core alpha-weighted RRF search over `allowed`. Used both for the
    traditional single-pool path and as the per-scope fetch inside
    scoped_retrieval_merge.
    """
    if not allowed:
        return []

    if not query_expansion or not query_expansion.strip():
        return _search_within(model, faiss_index, bm25, chunks, query_original, limit, allowed, boost_factor=boost_factor)

    combined_query_text = f"{query_original} {query_expansion}"
    has_tokens = bool(extract_discriminating_tokens(combined_query_text))
    fetch_k = len(allowed) if has_tokens else min(limit * 10, len(allowed))

    rrf_orig = _rrf_scores(query_original, model, faiss_index, bm25, chunks, allowed, fetch_k)

    try:
        rrf_exp = _rrf_scores(query_expansion, model, faiss_index, bm25, chunks, allowed, fetch_k)
    except Exception as exc:
        logger.warning("search_weighted: expansion retrieval failed (%s), falling back to original", exc)
        rrf_orig = _apply_lexical_boost(combined_query_text, rrf_orig, chunks, boost_factor)
        top_indices = sorted(rrf_orig, key=rrf_orig.__getitem__, reverse=True)[:limit]
        return [
            SearchHit(
                source_path=chunks[i].source_path,
                doc_family=chunks[i].doc_family,
                score=rrf_orig[i],
                content=chunks[i].content,
                page=chunks[i].page,
                section=chunks[i].section,
            )
            for i in top_indices
        ]

    all_ids = set(rrf_orig) | set(rrf_exp)
    combined: dict[int, float] = {
        i: alpha * rrf_orig.get(i, 0.0) + (1 - alpha) * rrf_exp.get(i, 0.0)
        for i in all_ids
    }
    combined = _apply_lexical_boost(combined_query_text, combined, chunks, boost_factor)
    top_indices = sorted(combined, key=combined.__getitem__, reverse=True)[:limit]
    return [
        SearchHit(
            source_path=chunks[i].source_path,
            doc_family=chunks[i].doc_family,
            score=combined[i],
            content=chunks[i].content,
            page=chunks[i].page,
            section=chunks[i].section,
        )
        for i in top_indices
    ]


def search_weighted(
    settings: Settings,
    query_original: str,
    query_expansion: str | None,
    top_k: int | None = None,
    alpha: float = 0.5,
    family_filter: list[str] | None = None,
    scoped: bool | None = None,
) -> list[SearchHit]:
    """Hybrid weighted retrieval: alpha * RRF(original) + (1-alpha) * RRF(expansion).

    Falls back to a plain search(query_original) if query_expansion is empty
    or if the expansion retrieval raises an exception. See search() for the
    family_filter / scoped retrieval semantics — identical here.
    """
    model, faiss_index, bm25, chunks = load_index(settings)
    limit = top_k or settings.top_k
    use_scoped = settings.scoped_retrieval if scoped is None else scoped

    if family_filter:
        allowed = {i for i, c in enumerate(chunks) if c.doc_family in family_filter}
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            limit, allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    if not use_scoped:
        allowed = set(range(len(chunks)))
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            limit, allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    present_families = {c.doc_family for c in chunks}
    scopes = [f for f in settings.family_slots if f in present_families]
    if not scopes:
        allowed = set(range(len(chunks)))
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            limit, allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    def fetch_fn(family: str, k: int) -> list[SearchHit]:
        fam_allowed = {i for i, c in enumerate(chunks) if c.doc_family == family}
        return _search_weighted_within(
            model, faiss_index, bm25, chunks, query_original, query_expansion,
            k, fam_allowed, alpha, boost_factor=settings.lexical_boost_factor,
        )

    return scoped_retrieval_merge(scopes, settings.family_slots, limit, fetch_fn)
