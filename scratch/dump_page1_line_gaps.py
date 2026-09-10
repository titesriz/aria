"""
DISPOSABLE diagnostic script — read-only, writes nothing, touches no pipeline code.

Purpose: dump the vertical gaps between text lines on PAGE 1 of a handful of PDFs,
to eyeball where the ratio (gap_y / font_size) splits into two clusters — small
ratios for a wrapped/same-notion line break, large ratios for a real section/block
break (new title vs subtitle, etc). This is purely observational: it does NOT pick
or hardcode a threshold. That decision belongs to the future title-extraction logic,
once a human has looked at this dump.

Uses page.get_text("dict") and descends to raw spans (not PyMuPDF's blocks/lines
grouping-for-semantics — lines are used here only as a display unit: a "line" is a
group of spans sharing the same y0, which is a fine unit for reading the dump, but
the gap/ratio decision itself is not derived from PyMuPDF's block boundaries).

Run: python3 scratch/dump_page1_line_gaps.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # pymupdf

REPO_ROOT = Path(__file__).resolve().parent.parent

# Edit this list to add/remove PDFs.
PDF_PATHS = [
    REPO_ROOT / "Ressources" / "PLU bioclimatique" / "Annexes" / "Plans SUP" / "ASUP2AD5.pdf",
    REPO_ROOT / "Ressources" / "PLU bioclimatique" / "OAP" / "Sectorielles" / "OAP_BARTHOLOME_BRANCION.pdf",
    REPO_ROOT / "Ressources" / "PLU bioclimatique" / "PADD" / "PADD.pdf",
    REPO_ROOT / "Ressources" / "PLU bioclimatique" / "Annexes" / "Addenda" / "ANNAD1_2025_12_19.pdf",
]

TRUNCATE_LEN = 40


def truncate(s: str, n: int = TRUNCATE_LEN) -> str:
    s = " ".join(s.split())  # collapse internal whitespace for a tidy single-line display
    return s if len(s) <= n else s[: n - 1] + "…"


def extract_lines(page):
    """
    Return a list of dicts, one per visual "line" (spans sharing the same y0),
    sorted by y0 ascending. Each dict: text, y0, y1, size (max span size on the
    line), font (font of the largest span, as a representative value).

    Built from raw spans, not trusted to PyMuPDF's own block/line semantics for
    the gap decision — but a span-level "line" grouping by shared y0 is used
    purely so the dump reads like text instead of a flat span soup.
    """
    raw = page.get_text("dict")
    spans = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # skip non-text (image) blocks
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                spans.append(
                    dict(text=text, y0=y0, y1=y1, size=span.get("size", 0.0), font=span.get("font", ""))
                )

    if not spans:
        return []

    # Group spans into display lines by shared y0 (rounded, to tolerate sub-pixel jitter).
    spans.sort(key=lambda s: (round(s["y0"]), s["y0"]))
    lines = []
    current_key = None
    current = []
    for s in spans:
        key = round(s["y0"])
        if current and key != current_key:
            lines.append(current)
            current = []
        current.append(s)
        current_key = key
    if current:
        lines.append(current)

    display_lines = []
    for group in lines:
        text = "".join(s["text"] for s in group)
        y0 = min(s["y0"] for s in group)
        y1 = max(s["y1"] for s in group)
        size = max(s["size"] for s in group)
        # representative font: from the span with the largest size on this line
        font = max(group, key=lambda s: s["size"])["font"]
        display_lines.append(dict(text=text, y0=y0, y1=y1, size=size, font=font))

    display_lines.sort(key=lambda l: l["y0"])
    return display_lines


def dump_pdf(pdf_path: Path):
    print("=" * 100)
    print(f"PDF: {pdf_path}")
    print("=" * 100)

    if not pdf_path.exists():
        print(f"  [SKIP] file not found: {pdf_path}")
        return

    doc = fitz.open(str(pdf_path))
    page = doc[0]
    lines = extract_lines(page)

    if not lines:
        print("  aucun span texte")
        return

    print(
        f"  {'ratio':>7} | {'gap_y':>8} | {'size':>6} | {'police':>4} | ligne courante -> ligne suivante"
    )
    print(f"  {'-'*7}-+-{'-'*8}-+-{'-'*6}-+-{'-'*4}-+-{'-'*60}")

    ratios = []
    for i in range(len(lines) - 1):
        cur = lines[i]
        nxt = lines[i + 1]
        gap_y = nxt["y0"] - cur["y1"]
        size = cur["size"]
        ratio = gap_y / size if size else float("nan")
        police_change = "oui" if (cur["size"] != nxt["size"] or cur["font"] != nxt["font"]) else "non"

        ratios.append(ratio)
        cur_txt = truncate(cur["text"])
        nxt_txt = truncate(nxt["text"])
        print(
            f"  {ratio:7.2f} | {gap_y:8.2f} | {size:6.2f} | {police_change:>4} | "
            f"\"{cur_txt}\" -> \"{nxt_txt}\""
        )

    print()
    print(f"  Résumé ratios (triés): {[round(r, 2) for r in sorted(ratios)]}")
    print()


def main():
    # Force UTF-8 stdout regardless of locale, so accented characters always render.
    sys.stdout.reconfigure(encoding="utf-8")
    for pdf_path in PDF_PATHS:
        dump_pdf(pdf_path)


if __name__ == "__main__":
    main()
