"""
DISPOSABLE diagnostic script — read-only, writes nothing, touches no pipeline code.

Purpose: on page 1 of a "planche à encart" (graphic plan sheet with a title inset,
e.g. ASUP2AD5), test the hypothesis that the title inset (black frame, white
background, text starting with "Annexes") is a detectable VECTOR-DRAWN rectangle via
page.get_drawings(). Goal: decide whether the inset can be isolated by its frame (the
cleanest detector) or whether extraction must fall back to a text anchor ("Annexes")
+ proximity instead.

This script only OBSERVES — it does not pick a threshold or hardcode extraction logic.

Run: python3 scratch/detect_encart_rect.py
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import fitz  # pymupdf

REPO_ROOT = Path(__file__).resolve().parent.parent

# Edit this list to add/remove PDFs. Style matches scratch/dump_page1_line_gaps.py.
PDF_PATHS = [
    REPO_ROOT / "Ressources" / "PLU bioclimatique" / "Annexes" / "Plans SUP" / "ASUP2AD5.pdf",
    REPO_ROOT / "Ressources" / "PLU bioclimatique" / "Annexes" / "Plans autres périmètres" / "A15152_01A04_2025_12_19.pdf",
]

MIN_RECT_SIDE = 40.0  # pt — both width and height must exceed this to be a plausible frame
TRUNCATE_LEN = 40
TOP_N_TEXTS = 6

# Anchor words the task expects to find inside the real title inset — used only to
# report whether a text anchor is even available on this page, not to decide anything.
ANCHOR_WORDS = ["annexe", "servitud", "utilisation", "energ", "circulat", "aerien"]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def truncate(s: str, n: int = TRUNCATE_LEN) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def extract_text_spans(page):
    """Flat list of {text, bbox, cx, cy} for every non-empty text span on the page."""
    spans = []
    raw = page.get_text("dict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                spans.append(
                    dict(text=text, bbox=(x0, y0, x1, y1), cx=(x0 + x1) / 2, cy=(y0 + y1) / 2)
                )
    return spans


def extract_rect_candidates(page):
    """
    Plausible rectangular frames from page.get_drawings(): for each drawing, use its
    overall bbox (PyMuPDF already computes this as d['rect'] from the drawing's
    items — a rectangle 're' item or a closed 4-segment 'l' path both collapse to the
    same bounding rect) and keep it only if both width and height exceed MIN_RECT_SIDE.
    """
    candidates = []
    for d in page.get_drawings():
        rect = d.get("rect")
        if rect is None:
            continue
        w, h = rect.width, rect.height
        if w > MIN_RECT_SIDE and h > MIN_RECT_SIDE:
            candidates.append(
                dict(
                    bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                    width=w,
                    height=h,
                    dtype=d.get("type"),  # 'f' fill, 's' stroke, 'fs' fill+stroke
                    fill=d.get("fill"),
                    color=d.get("color"),
                    closed=d.get("closePath"),
                )
            )
    return candidates


def point_in_bbox(cx, cy, bbox):
    x0, y0, x1, y1 = bbox
    return x0 <= cx <= x1 and y0 <= cy <= y1


def dump_pdf(pdf_path: Path):
    print("=" * 100)
    print(f"PDF: {pdf_path}")
    print("=" * 100)

    if not pdf_path.exists():
        print(f"  [SKIP] file not found: {pdf_path}")
        return

    doc = fitz.open(str(pdf_path))
    page = doc[0]

    spans = extract_text_spans(page)
    print(f"  Spans texte trouvés sur la page 1 : {len(spans)}")
    if not spans:
        print("  aucun span texte")

    anchor_hits = [
        s["text"] for s in spans if any(w in strip_accents(s["text"]).lower() for w in ANCHOR_WORDS)
    ]
    if anchor_hits:
        print(f"  Ancre texte disponible ({len(anchor_hits)} span(s) matchant {ANCHOR_WORDS}):")
        for t in anchor_hits[:10]:
            print(f"    - {truncate(t, 60)!r}")
    else:
        print(
            f"  Aucun span ne contient un des mots-ancre attendus {ANCHOR_WORDS} "
            f"— le texte de l'encart décrit dans la tâche n'apparaît pas sur cette page."
        )

    rects = extract_rect_candidates(page)
    print()
    print(f"  Rectangles vectoriels candidats (>{MIN_RECT_SIDE:.0f}x{MIN_RECT_SIDE:.0f} pt) : {len(rects)}")

    if not rects:
        print()
        print("  ⚠️ aucun rectangle vectoriel exploitable → détecter l'encart par l'ancre texte")
        return

    for r in rects:
        contained = [s for s in spans if point_in_bbox(s["cx"], s["cy"], r["bbox"])]
        r["n_spans"] = len(contained)
        r["sample_texts"] = [s["text"] for s in contained[:TOP_N_TEXTS]]

    rects.sort(key=lambda r: -r["n_spans"])

    print()
    print(
        f"  {'bbox (x0,y0,x1,y1)':>45} | {'larg':>7} | {'haut':>7} | type | fill/color | n_spans | échantillon"
    )
    print(f"  {'-'*45}-+-{'-'*7}-+-{'-'*7}-+------+------------+---------+{'-'*40}")
    for r in rects[:20]:  # cap console noise; sorted so the interesting ones are on top
        x0, y0, x1, y1 = r["bbox"]
        bbox_str = f"({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})"
        fc = r["fill"] if r["fill"] is not None else r["color"]
        fc_str = truncate(str(fc), 20) if fc is not None else "-"
        sample = " / ".join(truncate(t, 20) for t in r["sample_texts"])
        print(
            f"  {bbox_str:>45} | {r['width']:7.1f} | {r['height']:7.1f} | {r['dtype']:>4} | "
            f"{fc_str:>10} | {r['n_spans']:7d} | {sample}"
        )

    # Does the top rectangle's contained-span set look like the described title inset?
    top = rects[0]
    top_texts_norm = [strip_accents(t).lower() for t in top["sample_texts"]]
    anchor_in_top = any(any(w in t for w in ANCHOR_WORDS) for t in top_texts_norm)

    print()
    if top["n_spans"] > 0 and anchor_in_top:
        print(
            f"  ✅ encart = rectangle vectoriel {top['bbox']} contenant {top['n_spans']} spans titre"
        )
    elif top["n_spans"] > 0:
        print("  ⚠️ rectangles présents mais aucun ne correspond à l'encart titre")
        print(
            f"     (le rectangle avec le plus de spans, {top['bbox']}, contient "
            f"{top['n_spans']} span(s) mais pas les mots-ancre attendus — "
            f"échantillon: {top['sample_texts']})"
        )
    else:
        print("  ⚠️ rectangles présents mais aucun ne correspond à l'encart titre")
        print("     (aucun rectangle candidat ne contient le moindre span texte)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for pdf_path in PDF_PATHS:
        dump_pdf(pdf_path)


if __name__ == "__main__":
    main()
