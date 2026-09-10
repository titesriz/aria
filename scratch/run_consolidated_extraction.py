"""
Runner for the canonical extractor (scratch/extract_sommaire.py):
  PART 1 — non-regression: re-run on the 6 already-validated documents,
           compare against their last-good saved trees.
  PART 2 — process every PDF in Ressources/.../Rapport de présentation/,
           emitting one <name>_sommaire_tree.json per file.

Extraction only. Nothing written to Notion.
Run: python3 scratch/run_consolidated_extraction.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_sommaire import REPO_ROOT, SCRATCH_DIR, process, tree_level_summary  # noqa: E402

RES = REPO_ROOT / "Ressources" / "PLU bioclimatique"
RP_DIR = RES / "Rapport de présentation"

NON_REGRESSION = [
    ("REG1_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 1" / "REG1_MS1.pdf",
     SCRATCH_DIR / "reg1_sommaire_tree.json"),
    ("REG2A1_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A1_MS1.pdf",
     SCRATCH_DIR / "reg2a1_sommaire_tree.json"),
    ("REG2A10_1DE2_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A10_1DE2_MS1.pdf",
     SCRATCH_DIR / "reg2a10_1de2_ms1_sommaire_tree.json"),
    ("REG2A10_2DE2_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A10_2DE2_MS1.pdf",
     SCRATCH_DIR / "reg2a10_2de2_ms1_sommaire_tree.json"),
    ("RP_CHOIX.pdf", RP_DIR / "RP_CHOIX.pdf", SCRATCH_DIR / "rp_choix_sommaire_tree.json"),
    ("Rapport_presentation_MS1.pdf", RP_DIR / "Rapport_presentation_MS1.pdf",
     SCRATCH_DIR / "rapport_presentation_ms1_sommaire_tree.json"),
]


def load_saved_flat(path: Path):
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    flat = []

    def walk(n):
        flat.append((n.get("numero"), n.get("titre"), n.get("page_debut"), n.get("niveau")))
        for c in n.get("enfants", []):
            walk(c)

    for n in d.get("arbre", []):
        walk(n)
    anchor = d.get("anchor_check", {})
    return flat, anchor.get("match_rate"), len(d.get("warnings", []))


def flatten_new(flat_order):
    return [(e.numero, e.titre, e.page_debut, e.niveau) for e in flat_order]


def diff_against_saved(name, saved_path, new_flat_order, new_anchor, new_warnings):
    saved = load_saved_flat(saved_path)
    new_flat = flatten_new(new_flat_order)
    lines = [f"--- {name} ---"]
    if saved is None:
        lines.append(f"(no saved tree at {saved_path.name} — nothing to compare against)")
        lines.append(f"NEW: {len(new_flat)} entries, anchor={new_anchor}, warnings={len(new_warnings)}")
        return "\n".join(lines)

    old_flat, old_anchor, old_warn_count = saved
    old_content = [(o[0], o[1], o[2]) for o in old_flat]
    new_content = [(n[0], n[1], n[2]) for n in new_flat]

    if old_content == new_content:
        lines.append(f"Extraction unchanged: {len(new_content)} entries, identical (numero, titre, page_debut) sequence.")
    else:
        lines.append("*** EXTRACTION ITSELF DIFFERS — STOP, do not silently accept ***")
        lines.append(f"  entry count: old={len(old_content)} new={len(new_content)}")
        n = min(len(old_content), len(new_content))
        shown = 0
        for i in range(n):
            if old_content[i] != new_content[i] and shown < 15:
                lines.append(f"  [{i}] old={old_content[i]!r}\n       new={new_content[i]!r}")
                shown += 1

    old_hist: dict[int, int] = {}
    for _, _, _, lvl in old_flat:
        old_hist[lvl] = old_hist.get(lvl, 0) + 1
    new_hist: dict[int, int] = {}
    for _, _, _, lvl in new_flat:
        new_hist[lvl] = new_hist.get(lvl, 0) + 1
    level_match = "IDENTICAL" if old_hist == new_hist else "DIFFERS (see histograms)"
    lines.append(f"Levels: {level_match} | before={dict(sorted(old_hist.items()))} after={dict(sorted(new_hist.items()))}")
    lines.append(f"Anchor match: before={old_anchor} after={new_anchor}")
    lines.append(f"Warnings count: before={old_warn_count} after={len(new_warnings)}")
    return "\n".join(lines)


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    print("#" * 100)
    print("# PART 1 — CONSOLIDATION NON-REGRESSION REPORT")
    print("#" * 100)
    print()
    for name, path, saved_path in NON_REGRESSION:
        output, flat_order, doc, col_report = process(path, out_path=None)
        print(diff_against_saved(name, saved_path, flat_order, output["anchor_check"]["match_rate"], output["warnings"]))
        print()

    # Regenerate RP_CHOIX's saved tree with the consolidated logic (per task).
    for name, path, saved_path in NON_REGRESSION:
        if name == "RP_CHOIX.pdf":
            process(path, out_path=saved_path)
            print(f"(RP_CHOIX.pdf regenerated at consolidated logic -> {saved_path.name})")
            print()

    print("#" * 100)
    print("# PART 2 — Rapport de présentation/ folder")
    print("#" * 100)
    print()
    pdfs = sorted(RP_DIR.glob("*.pdf"))
    print(f"Folder: {RP_DIR}")
    print(f"PDF files found ({len(pdfs)}): {[p.name for p in pdfs]}")
    print()

    header = f"{'fichier':38} | {'col':>3} | {'sommaire pages':>14} | {'entries':>7} | {'levels':^7} | {'anchor':>7} | {'warn':>4}"
    print(header)
    print("-" * len(header))

    flagged = []
    for pdf_path in pdfs:
        out_path = SCRATCH_DIR / f"{pdf_path.stem.lower()}_sommaire_tree.json"
        output, flat_order, doc, col_report = process(pdf_path, out_path=out_path)
        n_entries = len(flat_order)
        anchor = output["anchor_check"]["match_rate"]
        n_warn = len(output["warnings"])
        n_toc_pages = len(output["sommaire_pages_detected"].get("physical_indices_0based", []))

        if n_toc_pages == 0:
            print(f"{pdf_path.name:38} | {'n/a':>3} | {'0':>14} | {0:>7} | {'n/a':^7} | {'n/a':>7} | {n_warn:>4}   *** AUCUN SOMMAIRE DÉTECTÉ ***")
            flagged.append((pdf_path.name, "aucun sommaire détecté"))
            continue

        col_str = "/".join(str(c) for c in col_report)
        anchor_str = f"{anchor:.0%}" if anchor is not None else "n/a"
        levels = tree_level_summary(flat_order)
        levels_str = f"{min(levels)}-{max(levels)}" if levels else "n/a"
        print(f"{pdf_path.name:38} | {col_str:>3} | {n_toc_pages:>14} | {n_entries:>7} | {levels_str:^7} | {anchor_str:>7} | {n_warn:>4}")

        if anchor is not None and anchor < 1.0:
            flagged.append((pdf_path.name, f"anchor match {anchor:.0%}"))

    print()
    print("=== FLAGGED (anchor < 100% or no sommaire) ===")
    if not flagged:
        print("(none — every file with a detected sommaire hit 100% anchor match)")
    else:
        for name, reason in flagged:
            print(f"  {name}: {reason}")


if __name__ == "__main__":
    main()
