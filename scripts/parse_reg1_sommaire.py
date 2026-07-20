"""One-off parse of REG1_MS1.pdf's front-matter SOMMAIRE (pages 2-5) into an
{article_code: {theme, page}} table, plus a coverage diff against the
whitelist the indexer already derives from the live corpus
(build_article_whitelist, eval/article_whitelist.json).

Not part of the shipped pipeline -- run manually, writes
eval/ontology/reg1_sommaire.json. See SESSION_STATE.md, 2026-07-20, for the
task this supports (concept-to-article routing proposal, T4).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from aria_rag.indexer import Chunk, build_article_whitelist
from aria_rag.loader import read_pdf

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "eval" / "ontology" / "reg1_sommaire.json"
WHITELIST_PATH = ROOT / "eval" / "article_whitelist.json"

# The SOMMAIRE runs pages 2-5 in REG1_MS1.pdf -- page 6 is where "PARTIE 1"
# actually begins (confirmed by direct page-by-page inspection).
SOMMAIRE_PAGES = {2, 3, 4, 5}

# Same article-code family as indexer._ARTICLE_CODE_PATTERN, anchored to a
# line start (the TOC's own line structure) and tolerant of a stray trailing
# period some entries have right after the code (e.g. "UGSU.4.").
_CODE_START = re.compile(r"(?m)^((?:UG(?:SU)?|UV|N)\.\d+(?:\.\d+)*)\.?\s+")

# A run of 3+ dots is the TOC's leader between title and page number.
_DOT_LEADER = re.compile(r"\.{3,}")


def parse_sommaire(full_text: str) -> list[dict]:
    matches = list(_CODE_START.finditer(full_text))
    boundaries = [m.start() for m in matches] + [len(full_text)]
    entries = []
    for i, m in enumerate(matches):
        code = m.group(1)
        # Bounded by the NEXT code match, not the next dot-leader -- a
        # segment can run past its own page number into the following
        # page's running header/footer ("PLU DE PARIS 3 / 250 DÉCEMBRE
        # 2025") when an entry's title wraps across a page boundary. Only
        # the number immediately after the FIRST dot-leader is the real
        # page number; anything past it (including a later, unrelated
        # number like "2025" from that header) is discarded.
        segment = full_text[m.end(): boundaries[i + 1]]
        dot_match = _DOT_LEADER.search(segment)
        if dot_match:
            title_part = segment[: dot_match.start()]
            page_match = re.match(r"\s*(\d+)", segment[dot_match.end():])
            page = int(page_match.group(1)) if page_match else None
        else:
            # No dot-leader run (one entry, UGSU.2.3, has a single stray dot
            # instead -- a PDF-extraction quirk, not this parser's doing).
            # The FIRST number in the segment is the real page number; using
            # the LAST would grab a later, unrelated number from page-
            # boundary noise (the next page's "PLU DE PARIS N / 250
            # DÉCEMBRE 2025" header bleeding into this entry's segment).
            page_match = re.search(r"\d+", segment)
            page = int(page_match.group(0)) if page_match else None
            title_part = segment[: page_match.start()] if page_match else segment
        title = re.sub(r"\s+", " ", title_part).strip(" .")
        entries.append({"article_code": code, "theme": title, "page": page})
    return entries


def main() -> None:
    pdf_path = next(Path("Ressources").rglob("REG1_MS1.pdf"))
    doc = read_pdf(pdf_path)
    sommaire_text = "\n".join(text for pn, text in doc.pages if pn in SOMMAIRE_PAGES)

    entries = parse_sommaire(sommaire_text)
    sommaire_codes = {e["article_code"] for e in entries}

    if WHITELIST_PATH.exists():
        index_codes = set(json.loads(WHITELIST_PATH.read_text(encoding="utf-8")))
    else:
        chunks_path = ROOT / "data" / "index" / "chunks.json"
        chunks = [Chunk(**c) for c in json.loads(chunks_path.read_text(encoding="utf-8"))]
        index_codes = set(build_article_whitelist(chunks))

    only_in_sommaire = sorted(sommaire_codes - index_codes)
    only_in_index = sorted(index_codes - sommaire_codes)
    in_both = sorted(sommaire_codes & index_codes)

    output = {
        "source_pdf": str(pdf_path),
        "sommaire_pages": sorted(SOMMAIRE_PAGES),
        "entry_count": len(entries),
        "entries": entries,
        "coverage": {
            "sommaire_code_count": len(sommaire_codes),
            "index_whitelist_code_count": len(index_codes),
            "in_both_count": len(in_both),
            "only_in_sommaire": only_in_sommaire,
            "only_in_index_whitelist": only_in_index,
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Parsed {len(entries)} sommaire entries ({len(sommaire_codes)} distinct codes)")
    print(f"Index whitelist: {len(index_codes)} distinct codes")
    print(f"In both: {len(in_both)}")
    print(f"Only in sommaire (missing from index): {len(only_in_sommaire)} -> {only_in_sommaire}")
    print(f"Only in index (not in sommaire): {len(only_in_index)} -> {only_in_index[:30]}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
