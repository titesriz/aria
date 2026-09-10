"""
DISPOSABLE diagnostic script — read-only, writes only its own CSV report in
scratch/, touches no pipeline code, splits/modifies no PDF.

Purpose: survey PAGE 1 of every PDF under a target folder (default: the whole
"PLU bioclimatique" resource tree, 409 files) and, for each, report:
  - a page-1 LAYOUT TYPE (couverture/linéaire, sommaire dense, planche
    graphique/carte avec encart, page sans texte exploitable)
  - a FAISABILITÉ verdict for title extraction, based only on the methods
    already validated earlier in this study:
      * "linéaire" pages -> the gap-Y / largest-font-at-top heuristic
        (validated on PADD.pdf, OAP_BARTHOLOME_BRANCION.pdf)
      * "planche graphique avec encart" pages -> a LIGHTWEIGHT proxy only
        (an outlier-sized text span near the top of the page). This is NOT
        the full vector-frame detector (get_drawings(), scratch/
        detect_encart_rect.py) — that one is expensive (multi-second per
        file on dense map pages) and was only validated on 2 files so far
        (found a real legend frame on one, no title cartouche match on
        either). Running it on all 409 files is a deliberate non-goal here;
        this script flags candidates for that heavier follow-up instead of
        re-running it blindly at scale.

This is explicitly a FAST, CHEAP first pass (get_text + get_image_info only,
no get_drawings()) to prioritize which files need the heavier per-file
inspection next — not a final verdict.

Run: python3 scratch/survey_page1_titles.py
"""
from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

import fitz  # pymupdf

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = REPO_ROOT / "Ressources" / "PLU bioclimatique"
OUT_CSV = Path(__file__).resolve().parent / "page1_title_survey.csv"

# A span is a "large outlier" (candidate title) if its font size exceeds the
# page's median span size by this factor. Purely observational cutoff for
# this survey's proxy signal — NOT a production threshold.
OUTLIER_SIZE_FACTOR = 1.6
TOP_ZONE_RATIO = 0.35  # "near the top of the page" = within the top 35% of page height


def get_page1_spans(page):
    spans = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                spans.append(
                    dict(text=text, size=span.get("size", 0.0), font=span.get("font", ""), bbox=span["bbox"])
                )
    return spans


def image_area_ratio(page):
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return 0.0
    total = 0.0
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0
    for info in infos:
        x0, y0, x1, y1 = info["bbox"]
        total += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return min(total / page_area, 1.0)  # clip: overlapping images can exceed 1.0 unclipped


def looks_like_dense_toc(spans) -> bool:
    """Cheap heuristic reused from this study's earlier sommaire work: a page
    dominated by lines ending in a run of leader dots + trailing digits."""
    if len(spans) < 8:
        return False
    dotted = sum(1 for s in spans if ".." in s["text"] or s["text"].strip().rstrip(".").isdigit())
    return dotted / len(spans) > 0.15


def classify_page1(page):
    spans = get_page1_spans(page)
    n_spans = len(spans)
    img_ratio = image_area_ratio(page)
    page_h = page.rect.height

    if n_spans == 0:
        return dict(
            type_page1="aucun texte exploitable (image/scan pur)",
            faisabilite="NON — pas de span texte sur la page",
            detail=f"image_area_ratio={img_ratio:.2f}",
        )

    sizes = [s["size"] for s in spans if s["size"] > 0]
    median_size = statistics.median(sizes) if sizes else 0.0
    max_size = max(sizes) if sizes else 0.0

    outliers = [s for s in spans if median_size and s["size"] >= median_size * OUTLIER_SIZE_FACTOR]
    outliers_near_top = [s for s in outliers if s["bbox"][1] <= page_h * TOP_ZONE_RATIO]

    # --- Couverture / page de titre, checked BEFORE the image-ratio branch ---
    # A full-bleed background photo/illustration behind a short title (e.g. the OAP
    # sectorielles covers) has image_area_ratio == 1.0 just like a dense map plan,
    # but structurally it's the same easy "couverture" case validated earlier in this
    # study (PADD.pdf, OAP_BARTHOLOME_BRANCION.pdf): very few spans, one clearly
    # larger title span. Checking span COUNT first (independent of image coverage)
    # avoids misclassifying these as "planche graphique" (caught during this survey:
    # OAP_BARTHOLOME_BRANCION.pdf itself was falling into that bucket before this
    # ordering fix, contradicting this session's own earlier validated result on it).
    if n_spans <= 20:
        if max_size >= median_size * 1.2:
            return dict(
                type_page1="couverture / page de titre (linéaire)",
                faisabilite="OUI — plus grande taille de police en haut de page (méthode gap-Y validée sur PADD.pdf, OAP_BARTHOLOME_BRANCION.pdf)",
                detail=f"n_spans={n_spans}, max_size={max_size:.1f}, median_size={median_size:.1f}, image_area_ratio={img_ratio:.2f}",
            )
        return dict(
            type_page1="couverture / page de titre (linéaire)",
            faisabilite="INCERTAIN — peu de spans mais pas de taille de police clairement dominante, à vérifier au cas par cas",
            detail=f"n_spans={n_spans}, max_size={max_size:.1f}, median_size={median_size:.1f}, image_area_ratio={img_ratio:.2f}",
        )

    # --- Planche graphique / carte (image-dominated) ---
    if img_ratio > 0.35 or n_spans > 400:
        if outliers_near_top:
            sample = outliers_near_top[0]["text"]
            return dict(
                type_page1="planche graphique (carte/plan) avec encart texte",
                faisabilite=(
                    f"PROBABLE — span de taille nettement supérieure à la médiane "
                    f"({outliers_near_top[0]['size']:.1f}pt vs médiane {median_size:.1f}pt) "
                    f"détecté en haut de page (candidat: {sample!r}) ; à confirmer par "
                    f"détection de cadre vectoriel (get_drawings, non exécutée ici)"
                ),
                detail=f"n_spans={n_spans}, image_area_ratio={img_ratio:.2f}, n_outliers_top={len(outliers_near_top)}",
            )
        return dict(
            type_page1="planche graphique (carte/plan) avec encart texte",
            faisabilite=(
                "INCERTAIN — aucun span nettement plus grand détecté en haut de page ; "
                "cas potentiellement proche de ASUP2AD5 (labels de carte uniquement, "
                "pas de cartouche titre repérable par la taille de police)"
            ),
            detail=f"n_spans={n_spans}, image_area_ratio={img_ratio:.2f}",
        )

    # --- Sommaire / table des matières dense ---
    if looks_like_dense_toc(spans):
        return dict(
            type_page1="sommaire / table des matières (linéaire, dense)",
            faisabilite="OUI — méthode d'ancrage titre validée (études REG1/REG2 de cette session)",
            detail=f"n_spans={n_spans}",
        )

    # --- Linéaire courant (n_spans > 20, not TOC-shaped, not image-dominated) ---
    return dict(
        type_page1="linéaire (texte courant)",
        faisabilite="OUI — pas de contrainte de mise en page particulière, méthode gap-Y applicable",
        detail=f"n_spans={n_spans}, max_size={max_size:.1f}, median_size={median_size:.1f}",
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    pdf_paths = sorted(TARGET_DIR.rglob("*.pdf"))
    print(f"Dossier cible : {TARGET_DIR}")
    print(f"PDF trouvés : {len(pdf_paths)}")
    print()

    rows = []
    for i, pdf_path in enumerate(pdf_paths, 1):
        rel = pdf_path.relative_to(REPO_ROOT)
        try:
            doc = fitz.open(str(pdf_path))
            if doc.page_count == 0:
                result = dict(type_page1="PDF sans page", faisabilite="NON", detail="")
            else:
                result = classify_page1(doc[0])
            doc.close()
        except Exception as e:  # never crash the whole survey on one bad file
            result = dict(type_page1="ERREUR OUVERTURE", faisabilite="NON", detail=str(e))

        rows.append(dict(fichier=str(rel), **result))
        if i % 50 == 0:
            print(f"  ... {i}/{len(pdf_paths)} traités")

    OUT_CSV.parent.mkdir(exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["fichier", "type_page1", "faisabilite", "detail"])
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"CSV écrit : {OUT_CSV} ({len(rows)} lignes)")

    # --- console summary ---
    print()
    print("=== RÉSUMÉ PAR TYPE DE PAGE 1 ===")
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type_page1"], []).append(r)
    for t, items in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(items):4d}  {t}")

    print()
    print("=== RÉSUMÉ FAISABILITÉ (préfixe du verdict) ===")
    by_verdict_prefix = {}
    for r in rows:
        prefix = r["faisabilite"].split(" — ")[0]
        by_verdict_prefix.setdefault(prefix, []).append(r)
    for v, items in sorted(by_verdict_prefix.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(items):4d}  {v}")

    print()
    print("=== CROISEMENT type × faisabilité ===")
    cross = {}
    for r in rows:
        key = (r["type_page1"], r["faisabilite"].split(" — ")[0])
        cross[key] = cross.get(key, 0) + 1
    for (t, v), n in sorted(cross.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  | {t:55s} | {v}")


if __name__ == "__main__":
    main()
