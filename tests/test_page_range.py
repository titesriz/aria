"""Regression tests for page_start/page_end attribution (src/aria_rag/indexer.py,
retriever.py) and citation formatting (retriever.py, cli.py, api.py).

Chunk.page previously recorded only the chunk's STARTING page — a chunk
spanning pages 19-21 was attributed to page 19 alone, undercounting which
pages a citation actually covers. This adds page_end via the same
bisection already used for `page`, applied to the chunk's end offset.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.indexer import Chunk, _page_at_offset, extract_chunks_from_pdf
from aria_rag.retriever import format_page_citation

_REG1_PDF = Path(__file__).resolve().parents[1] / (
    "Ressources/PLU/75 Paris/PLU Bioclimatique/Règlement/Pièces écrites/Tome 1/REG1.pdf"
)


# ---------------------------------------------------------------------------
# _page_at_offset — the shared bisection both page and page_end are built on
# ---------------------------------------------------------------------------

def test_page_at_offset_multi_page_span():
    # 3 pages: page 1 spans [0, 100), page 2 spans [100, 250), page 3 spans [250, 400)
    page_starts = [0, 100, 250]
    page_numbers = [1, 2, 3]
    assert _page_at_offset(page_starts, page_numbers, 0) == 1
    assert _page_at_offset(page_starts, page_numbers, 99) == 1
    assert _page_at_offset(page_starts, page_numbers, 100) == 2
    assert _page_at_offset(page_starts, page_numbers, 249) == 2
    assert _page_at_offset(page_starts, page_numbers, 250) == 3
    assert _page_at_offset(page_starts, page_numbers, 399) == 3


def test_page_at_offset_empty():
    assert _page_at_offset([], [], 0) is None


# ---------------------------------------------------------------------------
# extract_chunks_from_pdf — real corpus chunks, both multi- and single-page
# ---------------------------------------------------------------------------

def _reg1_chunks():
    return extract_chunks_from_pdf(_REG1_PDF, chunk_size=1200, chunk_overlap=200, min_alpha_ratio=0.55)


def test_known_multi_page_chunk_gets_correct_range():
    """REG1-46 (table of contents tail) starts on page 3 and its last text
    — "PLU DE PARIS 4 / 250 DÉCEMBRE 2025", page 4's own footer — spills
    onto page 4, confirmed directly against the physical PDF.
    """
    chunks = _reg1_chunks()
    c = next(x for x in chunks if x.chunk_id == "REG1-46")
    assert c.page == 3
    assert c.page_end == 4
    assert c.page_end > c.page


def test_known_single_page_chunk_has_start_equal_end():
    chunks = _reg1_chunks()
    c = next(x for x in chunks if x.chunk_id == "REG1-0")
    assert c.page == 2
    assert c.page_end == 2


def test_all_chunks_page_end_gte_page_start():
    """Global invariant across a real document: no chunk's end page can
    precede its start page.
    """
    chunks = _reg1_chunks()
    with_pages = [c for c in chunks if c.page is not None and c.page_end is not None]
    assert with_pages  # sanity: the document does produce page-attributed chunks
    for c in with_pages:
        assert c.page_end >= c.page, f"{c.chunk_id}: page={c.page} page_end={c.page_end}"


def test_page_end_never_leaks_into_content():
    """page_end is metadata only — it must never be concatenated into
    Chunk.content, which feeds BM25 tokenization and the embedding model.
    Checked against REG1-46's exact known text (page=3, page_end=4): content
    must start/end exactly where the real document text does, with no
    injected page marker.
    """
    chunks = _reg1_chunks()
    c = next(x for x in chunks if x.chunk_id == "REG1-46")
    assert c.page_end == 4
    assert c.content.startswith("UGSU.2.3 Dispositions applicables aux interventions sur les constructions existantes")
    assert c.content.endswith("PLU DE PARIS 4 / 250 D ÉCEMBRE 2025")


# ---------------------------------------------------------------------------
# format_page_citation
# ---------------------------------------------------------------------------

def test_format_page_citation_single_page():
    assert format_page_citation(19, 19) == "p. 19"


def test_format_page_citation_no_end_page_treated_as_single():
    assert format_page_citation(19, None) == "p. 19"


def test_format_page_citation_range():
    assert format_page_citation(19, 21) == "p. 19–21"


def test_format_page_citation_unknown_page():
    assert format_page_citation(None, None) is None
    assert format_page_citation(None, 21) is None


# ---------------------------------------------------------------------------
# Chunk schema
# ---------------------------------------------------------------------------

def test_chunk_page_end_defaults_to_none():
    c = Chunk(chunk_id="x-0", source_path="x.pdf", doc_family="annexes", content="hello")
    assert c.page is None
    assert c.page_end is None
