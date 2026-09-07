"""
One-shot title extraction for the Paris PLU corpus (412 PDFs).

Primary: largest-font text block on page 1 (visual_title).
Fallback: doc.metadata["title"], rejected if it looks like a filename.
Scratch-only, read-only against the project (no CSV/code modified).
"""
import csv
from pathlib import Path

import pymupdf

ROOT = Path("Ressources/PLU/75 Paris/PLU Bioclimatique")
OUT_PATH = Path("scratch/titre_extract.csv")

DOC_EXTS = (".odt", ".doc", ".docx")


def looks_like_filename(text: str, pdf_name: str) -> bool:
    t = text.strip()
    if not t:
        return False
    lower = t.lower()
    if lower.endswith(DOC_EXTS):
        return True
    stem = Path(pdf_name).stem.lower()
    if lower == pdf_name.lower() or lower == stem:
        return True
    return False


def extract_visual_title(page) -> str:
    data = page.get_text("dict")
    best_span = None
    best_size = -1.0
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # text blocks only
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                size = span.get("size", 0.0)
                y = span.get("bbox", [0, 0, 0, 0])[1]
                if best_span is None or size > best_size + 0.01 or (
                    abs(size - best_size) <= 0.01 and y < best_span[1]
                ):
                    # keep the largest; on a tie, prefer the one closer to top
                    if best_span is None or size > best_size + 0.01:
                        best_span = (text, y)
                        best_size = size
                    elif abs(size - best_size) <= 0.01 and y < best_span[1]:
                        best_span = (text, y)
    if best_span is None:
        return ""
    text = best_span[0].strip()
    if len(text) <= 1:
        return ""
    return text


def extract_title(pdf_path: Path, pdf_name: str):
    """Returns (titre_extrait, type_title_extract, error)."""
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        return "", "none", f"open_error: {e}"

    try:
        if doc.page_count == 0:
            return "", "none", "no_pages"

        try:
            page = doc.load_page(0)
            visual = extract_visual_title(page)
        except Exception as e:
            visual = ""

        if visual and not looks_like_filename(visual, pdf_name):
            return visual, "visual", ""

        meta_title = ""
        try:
            meta_title = (doc.metadata or {}).get("title", "") or ""
        except Exception:
            meta_title = ""
        meta_title = meta_title.strip()

        if meta_title and not looks_like_filename(meta_title, pdf_name):
            return meta_title, "metadata", ""

        return "", "none", ""
    finally:
        doc.close()


def main():
    pdf_files = sorted(ROOT.rglob("*.pdf"))
    rows = []
    counts = {"visual": 0, "metadata": 0, "none": 0}

    for p in pdf_files:
        rel = p.relative_to(ROOT).as_posix()
        titre, ttype, err = extract_title(p, p.name)
        counts[ttype] += 1
        row = {
            "chemin_relatif": rel,
            "pdf": p.name,
            "titre_extrait": titre,
            "type_title_extract": ttype,
        }
        if err:
            row["debug_error"] = err
        rows.append(row)

    has_errors = any("debug_error" in r for r in rows)
    fieldnames = ["chemin_relatif", "pdf", "titre_extrait", "type_title_extract"]
    if has_errors:
        fieldnames.append("debug_error")

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            if has_errors and "debug_error" not in r:
                r["debug_error"] = ""
            writer.writerow(r)

    print(f"Total PDFs processed: {len(rows)}")
    print(f"  visual:   {counts['visual']}")
    print(f"  metadata: {counts['metadata']}")
    print(f"  none:     {counts['none']}")
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
