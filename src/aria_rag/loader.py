from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

# Private-use-area glyphs (Wingdings/Symbol bullets etc.) that some PDF fonts
# map text runs onto; pypdf extracts them as raw PUA codepoints (U+F000-U+F0FF)
# that cp1252 consoles cannot encode.
_PUA_BULLET = re.compile(f"[{chr(0xF000)}-{chr(0xF0FF)}]")


def _normalize_pua(text: str) -> str:
    return _PUA_BULLET.sub("• ", text)


_HORIZONTAL_WS = re.compile(r"[ \t]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


def _normalize_whitespace(text: str) -> str:
    """Collapse horizontal whitespace per line while preserving line breaks.

    Table rows (Annexe V, X …) rely on newlines as column/row boundaries;
    a blanket " ".join(text.split()) merges them into one token stream.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_WS.sub(" ", line).strip() for line in text.split("\n")]
    collapsed = "\n".join(lines)
    return _EXCESS_NEWLINES.sub("\n\n", collapsed).strip()


@dataclass(slots=True)
class Document:
    path: str
    pages: list[tuple[int, str]]


def iter_pdf_paths(root: Path) -> list[Path]:
    return sorted(root.rglob("*.pdf"))


def read_pdf(path: Path) -> Document:
    """Extract text per page, keeping each page's real (1-indexed) PDF page number.

    Page numbers are assigned before dropping blank pages, so a blank page 5
    doesn't shift page 6's number down to 5 — attribution must match the
    actual document a reader would open.
    """
    reader = PdfReader(str(path))
    pages: list[tuple[int, str]] = []
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        cleaned = _normalize_whitespace(_normalize_pua(text))
        if cleaned:
            pages.append((page_num, cleaned))
    return Document(path=str(path), pages=pages)
