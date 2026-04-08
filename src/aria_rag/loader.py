from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(slots=True)
class Document:
    path: str
    text: str


def iter_pdf_paths(root: Path) -> list[Path]:
    return sorted(root.rglob("*.pdf"))


def read_pdf(path: Path) -> Document:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        cleaned = " ".join(text.split())
        if cleaned:
            pages.append(cleaned)
    return Document(path=str(path), text="\n".join(pages))

