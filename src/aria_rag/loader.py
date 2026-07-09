from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

# Private-use-area glyphs (Wingdings/Symbol bullets etc.) that some PDF fonts
# map text runs onto; pypdf extracts them as raw PUA codepoints (U+F000-U+F0FF)
# that cp1252 consoles cannot encode.
_PUA_BULLET = re.compile(f"[{chr(0xF000)}-{chr(0xF0FF)}]")

# Some fonts (mostly plan-tile legend labels in reglement_graphique/annexes)
# are effectively 2-byte (UTF-16/CID) encoded but get decoded as if 1-byte,
# leaving the zero high-byte of each code unit as a literal U+0000 before
# EVERY character of the run, e.g. "\x00M\x00é\x00t\x00a\x00l" for "Métal".
# U+0002 shows up the same way as part of a symbol-font marker pair
# ("\x02\x8c"), unrelated to the interleaving but equally non-printable
# noise. Confirmed corpus-wide: U+0000 never appears doubled (no
# "\x00\x00"), so it is strictly one-per-character and plain removal
# reconstructs the original text — it never functions as a word separator,
# so stripping it cannot merge two previously-distinct words.
_STRAY_CONTROL_CHARS = re.compile(f"[{chr(0x00)}{chr(0x02)}]")


def _normalize_font_artifacts(text: str) -> str:
    return _PUA_BULLET.sub("• ", _STRAY_CONTROL_CHARS.sub("", text))


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
        cleaned = _normalize_whitespace(_normalize_font_artifacts(text))
        if cleaned:
            pages.append((page_num, cleaned))
    return Document(path=str(path), pages=pages)
