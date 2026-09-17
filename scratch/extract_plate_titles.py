"""
Title extraction for the "Règlement graphique" family — 326 single-page
cartographic plates (Atlas 1/2/3), a DIFFERENT problem from the sommaire
extractor: there is no table of contents here. Each plate carries its own
title as embedded text, distinguishable from the surrounding map content
(street names, parcel numbers, tile-locator bands) by FONT ROLE, not by
position — position alone is unreliable (confirmed: the title cartouche can
be 1 or 2 text blocks, and a second block sharing the SAME font sits far down
the page as a disclaimer footer, not near the title).

Two families handled here, both purely from the embedded text layer (no
OCR):

  ATLAS 1 (thematic overlay maps, ~43 files): the title is the topmost
  cluster of spans in the EXACT font "Montserrat-Bold" (verified: every
  other bold-ish font present on these pages — SegoeUI-Bold, Arial-BoldMT,
  ArialNarrow-Bold — belongs to unrelated map labels, and matching on "any
  bold font" wrongly pulled in stray label fragments; matching the precise
  family name fixes this). A second, later Montserrat-Bold block (the "PLU
  approuvé par délibération..." disclaimer) is excluded by being far from
  the topmost cluster's y-position, not by content pattern.

  ATLAS 2 (grid tiles, ~145 files, e.g. B_07/I_11/D_11_A_251216): font role
  encodes meaning directly — Arial-BoldMT carries the document name and this
  tile's own reference ("Feuille I-11", rendered twice by a layout quirk),
  Arial-BoldItalicMT carries the arrondissement(s); plain ArialMT carries the
  "ʌ <neighbor tile> ʌ" locator, which is NOT part of this plate's own title
  and is dropped. All three informative spans sit in the topmost text row.

ATLAS 3 (Secteur Montmartre: 95 files, confirmed zero-text scanned images —
needs OCR; Secteur maisons et villas: cartouche text exists but a page can
carry multiple distinct SMV-coded titles, a different shape) is explicitly
OUT OF SCOPE for this script — held per direction, not attempted.

Extraction only. Writes a CSV for manual Notion merge — nothing written to
Notion by this script. Key = "Chemin relatif", matching the live Notion
schema's convention exactly (confirmed against existing rows): path relative
to Ressources/PLU bioclimatique/, POSIX separators, no leading slash.

Run: python3 scratch/extract_plate_titles.py
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pymupdf

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = Path(__file__).resolve().parent
RES_ROOT = REPO_ROOT / "Ressources" / "PLU bioclimatique"
GRAPHIQUE_DIR = RES_ROOT / "Règlement" / "Documents graphiques"

ATLAS1_TITLE_FONTS = ("Montserrat-Bold", "Montserrat-SemiBold")
# Two variants confirmed: thematic DG_* maps use Montserrat-Bold; the
# legend documents (LEG2000.pdf, both copies) use Montserrat-SemiBold for
# their own title line instead — same role, different weight.
ATLAS2_BOLD_FONTS = {"Arial-BoldMT", "Arial-BoldItalicMT"}
Y_CLUSTER_TOLERANCE_PT = 5  # same visual line only — tight, exact-font match
# makes a wider band unnecessary and avoids pulling in the next line down.
TITLE_MAX_Y0_PT = 60
# Sanity bound: every confirmed real title cartouche sits at y0 in [9,21].
# Without this, a single stray "MH" (Monument Historique marker) elsewhere
# on the page, rendered in the SAME font family as a real title elsewhere
# (Montserrat-Bold is also used for ordinary legend entries further down
# the page), gets treated as "the topmost cluster of that font" and wins by
# default — confirmed on BVNE.pdf, whose real title uses a DIFFERENT font
# entirely (the Atlas 2 Arial-tile pattern, despite sitting in an Atlas 1
# folder on disk — folder location is not a reliable signal for which
# pattern applies, only the fonts actually present on the page are).


def get_spans(page):
    """(y0, x0, font, text) for every character-level span, via rawdict so
    the actual decoded characters are read (PyMuPDF resolves the embedded
    font's encoding correctly here; text garbles seen when copy-pasting from
    some PDF viewers are a viewer-side artifact, not a PyMuPDF/extraction
    issue — confirmed by direct inspection)."""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                chars = "".join(ch["c"] for ch in span.get("chars", []))
                if chars.strip():
                    x0, y0 = span["bbox"][0], span["bbox"][1]
                    out.append((y0, x0, span.get("font", ""), chars))
    return out


def extract_atlas1(spans):
    title_spans = [s for s in spans if s[2] in ATLAS1_TITLE_FONTS]
    if not title_spans:
        return None
    min_y = min(s[0] for s in title_spans)
    if min_y > TITLE_MAX_Y0_PT:
        return None
    cluster = sorted((s for s in title_spans if s[0] - min_y <= Y_CLUSTER_TOLERANCE_PT), key=lambda s: s[1])
    return " ".join(s[3] for s in cluster)


def extract_atlas2(spans):
    bold_spans = [s for s in spans if s[2] in ATLAS2_BOLD_FONTS]
    if not bold_spans:
        return None
    min_y = min(s[0] for s in bold_spans)
    if min_y > TITLE_MAX_Y0_PT:
        return None
    cluster = sorted((s for s in bold_spans if s[0] - min_y <= Y_CLUSTER_TOLERANCE_PT), key=lambda s: s[1])
    seen, parts = set(), []
    for s in cluster:
        if s[3] not in seen:  # drop the "Feuille I-11" rendered-twice duplicate
            seen.add(s[3])
            parts.append(s[3])
    return ", ".join(parts)


def process_file(pdf_path: Path, family: str):
    # `family` (the file's own top-level Atlas folder) is NOT used to pick
    # which font pattern to try — confirmed unreliable (BVNE.pdf lives under
    # Atlas 1 but uses the Atlas 2 Arial-tile pattern). Try both; whichever
    # actually matches a font present near the page top wins.
    doc = pymupdf.open(str(pdf_path))
    spans = get_spans(doc[0])
    if not spans:
        return None, "no_text_ocr_required"
    title = extract_atlas2(spans) or extract_atlas1(spans)
    if title:
        return title, "ok"
    # Rare outlier: not actually a map plate (e.g. a multi-page index/TOC
    # document sitting in the same folder, confirmed on
    # "Consultation_des_planches_au_1_sur_2000.pdf" — 3 pages, no title
    # cartouche font at all, its own first line of plain text IS its title).
    # Flagged for manual review, never silently trusted.
    first_line = doc[0].get_text().split("\n", 1)[0].strip(" :")
    if first_line:
        return first_line, "fallback_first_line_REVIEW"
    return None, "no_title_font_found"


def main():
    rows = []
    atlas1_files = sorted((GRAPHIQUE_DIR / "Atlas 1").rglob("*.pdf"))
    atlas2_files = sorted((GRAPHIQUE_DIR / "Atlas 2").rglob("*.pdf"))

    for family, files in (("atlas1", atlas1_files), ("atlas2", atlas2_files)):
        for p in files:
            title, status = process_file(p, family)
            rows.append(dict(
                chemin_relatif=p.relative_to(RES_ROOT).as_posix(),
                pdf=p.name,
                titre_extrait=title or "",
                status=status,
            ))

    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"Atlas 1: {len(atlas1_files)} files | Atlas 2: {len(atlas2_files)} files | total: {len(rows)}")
    print(f"extracted: {ok} | failed: {len(rows) - ok}")
    for r in rows:
        if r["status"] != "ok":
            print(f"  FAILED [{r['status']}]: {r['chemin_relatif']}")

    out_path = SCRATCH_DIR / "plate_titles_atlas1_atlas2.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["chemin_relatif", "pdf", "titre_extrait", "status"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
