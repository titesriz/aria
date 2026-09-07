"""
Standalone, one-shot batch analysis script.

Scans a folder of PLU PDFs and extracts, per document:
  - three candidate titles (metadata / native TOC root / largest-font text on page 1)
  - table-of-contents structure (native doc.get_toc(), falling back to a
    regex-based heading scan of the first few pages when the PDF has no
    native TOC)

Does NOT touch any existing project code (aria_rag/*). Reads only.

Usage:
    venv/Scripts/python scratch/plu_toc_analysis.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT_DIR / "Ressources" / "PLU" / "75 Paris" / "PLU Bioclimatique"
OUT_DIR = Path(__file__).resolve().parent

CSV_PATH = OUT_DIR / "plu_analysis_summary.csv"
JSON_PATH = OUT_DIR / "plu_analysis_full.json"

FALLBACK_PAGES = 5  # how many pages to text-scan when there's no native TOC
PROGRESS_EVERY = 25

# ---------------------------------------------------------------------------
# Heading heuristics for the text-parsed fallback
# ---------------------------------------------------------------------------
# Order matters: first matching pattern wins for a given line.
# Level is either a fixed int, or a callable(match) -> int for numeric headings.

_NUMERIC_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")


def _numeric_level(m: re.Match) -> int:
    return m.group(1).count(".") + 1


HEADING_PATTERNS: list[tuple[re.Pattern, "int | callable"]] = [
    (re.compile(r"^TITRE\s+\S+", re.IGNORECASE), 1),
    (re.compile(r"^Chapitre\s+\S+", re.IGNORECASE), 2),
    (re.compile(r"^Sous-section\s+\S+", re.IGNORECASE), 3),
    (re.compile(r"^Section\s+\S+", re.IGNORECASE), 3),
    (re.compile(r"^Article\s+[A-Za-z]{1,4}[\.\d]*", re.IGNORECASE), 4),
    (_NUMERIC_HEADING_RE, _numeric_level),
]

MAX_HEADING_LINE_LEN = 150


def detect_heading(line: str) -> "int | None":
    """Return a heading level if `line` looks like a PLU-style heading, else None."""
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_HEADING_LINE_LEN:
        return None
    for pattern, level in HEADING_PATTERNS:
        m = pattern.match(stripped)
        if m:
            return level(m) if callable(level) else level
    return None


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_visual_title(page: "fitz.Page") -> str:
    """Largest-font text on the page, by comparing span sizes in get_text('dict')."""
    try:
        raw = page.get_text("dict")
    except Exception:
        return ""

    max_size = 0.0
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # not a text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size", 0.0)
                if size > max_size:
                    max_size = size

    if max_size <= 0:
        return ""

    pieces: list[str] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if abs(span.get("size", 0.0) - max_size) < 0.01:
                    text = span.get("text", "").strip()
                    if text:
                        pieces.append(text)

    return " ".join(pieces).strip()


def extract_native_toc(doc: "fitz.Document") -> list[dict]:
    toc = doc.get_toc()  # list of [level, title, page]
    return [
        {"level": level, "title": title.strip(), "page": page, "toc_source": "native"}
        for level, title, page in toc
    ]


def extract_text_parsed_toc(doc: "fitz.Document") -> list[dict]:
    entries: list[dict] = []
    n_pages = min(FALLBACK_PAGES, doc.page_count)
    for page_idx in range(n_pages):
        page = doc.load_page(page_idx)
        text = page.get_text("text") or ""
        for line in text.splitlines():
            level = detect_heading(line)
            if level is not None:
                entries.append(
                    {
                        "level": level,
                        "title": line.strip(),
                        "page": page_idx + 1,
                        "toc_source": "text_parsed",
                    }
                )
    return entries


def analyze_pdf(path: Path) -> dict:
    result: dict = {
        "relative_path": str(path.relative_to(TARGET_DIR)),
        "filename": path.name,
        "metadata_title": "",
        "toc_root_title": "",
        "visual_title": "",
        "toc_source": "",
        "n_levels": 0,
        "n_entries": 0,
        "toc_entries": [],
        "error": "",
    }

    doc = None
    try:
        doc = fitz.open(path)

        meta_title = (doc.metadata or {}).get("title") or ""
        result["metadata_title"] = meta_title.strip()

        if doc.page_count > 0:
            result["visual_title"] = extract_visual_title(doc.load_page(0))

        entries = extract_native_toc(doc)
        if entries:
            result["toc_source"] = "native"
        else:
            entries = extract_text_parsed_toc(doc)
            result["toc_source"] = "text_parsed" if entries else "none"

        result["toc_entries"] = entries
        result["toc_root_title"] = entries[0]["title"] if entries else ""
        result["n_entries"] = len(entries)
        result["n_levels"] = max((e["level"] for e in entries), default=0)

    except Exception as exc:  # noqa: BLE001 - deliberately broad, one-shot batch script
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not TARGET_DIR.is_dir():
        print(f"ERROR: target folder not found: {TARGET_DIR}", file=sys.stderr)
        sys.exit(1)

    pdf_paths = sorted(TARGET_DIR.rglob("*.pdf"))
    total = len(pdf_paths)
    print(f"Found {total} PDFs under {TARGET_DIR}")

    results: list[dict] = []
    for i, path in enumerate(pdf_paths, start=1):
        results.append(analyze_pdf(path))
        if i % PROGRESS_EVERY == 0 or i == total:
            print(f"  ...{i}/{total}")

    n_native = sum(1 for r in results if r["toc_source"] == "native")
    n_text_parsed = sum(1 for r in results if r["toc_source"] == "text_parsed")
    n_none = sum(1 for r in results if r["toc_source"] == "none")
    n_errors = sum(1 for r in results if r["error"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_fields = [
        "filename",
        "relative_path",
        "metadata_title",
        "visual_title",
        "toc_root_title",
        "toc_source",
        "n_levels",
        "n_entries",
        "error",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r[k] for k in csv_fields})

    with JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total PDFs analyzed : {total}")
    print(f"  native TOC        : {n_native}")
    print(f"  text_parsed TOC   : {n_text_parsed}")
    print(f"  no TOC detected   : {n_none}")
    print(f"  errored           : {n_errors}")
    print()
    print(f"Wrote {CSV_PATH}")
    print(f"Wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
