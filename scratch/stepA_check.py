"""STOP-AND-SHOW A: marker-free shape detection.
Part 1: run RP_20251017_MC1_HOTEL_DIEU alone, show detection result.
Part 2: non-regression on every OTHER doc (byte-identical check vs the
currently-saved trees, which already reflect all prior sessions' fixes)."""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_sommaire import REPO_ROOT, SCRATCH_DIR, process, tree_level_summary  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

RES = REPO_ROOT / "Ressources" / "PLU" / "75 Paris" / "PLU Bioclimatique"
RP_DIR = RES / "Rapport de présentation"

OTHER_DOCS = [
    ("REG1_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 1" / "REG1_MS1.pdf",
     SCRATCH_DIR / "reg1_sommaire_tree.json"),
    ("REG2A1_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A1_MS1.pdf",
     SCRATCH_DIR / "reg2a1_sommaire_tree.json"),
    ("REG2A10_1DE2_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A10_1DE2_MS1.pdf",
     SCRATCH_DIR / "reg2a10_1de2_ms1_sommaire_tree.json"),
    ("REG2A10_2DE2_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A10_2DE2_MS1.pdf",
     SCRATCH_DIR / "reg2a10_2de2_ms1_sommaire_tree.json"),
    ("RP_CHOIX.pdf", RP_DIR / "RP_CHOIX.pdf", SCRATCH_DIR / "rp_choix_sommaire_tree.json"),
    ("RP_DE.pdf", RP_DIR / "RP_DE.pdf", SCRATCH_DIR / "rp_de_sommaire_tree.json"),
    ("RP_DIAGNOSTIC.pdf", RP_DIR / "RP_DIAGNOSTIC.pdf", SCRATCH_DIR / "rp_diagnostic_sommaire_tree.json"),
    ("RP_EIE.pdf", RP_DIR / "RP_EIE.pdf", SCRATCH_DIR / "rp_eie_sommaire_tree.json"),
    ("RP_EVAL.pdf", RP_DIR / "RP_EVAL.pdf", SCRATCH_DIR / "rp_eval_sommaire_tree.json"),
    ("RP_INDIC.pdf", RP_DIR / "RP_INDIC.pdf", SCRATCH_DIR / "rp_indic_sommaire_tree.json"),
    ("RP_RNT.pdf", RP_DIR / "RP_RNT.pdf", SCRATCH_DIR / "rp_rnt_sommaire_tree.json"),
    ("Rapport_presentation_MS1.pdf", RP_DIR / "Rapport_presentation_MS1.pdf",
     SCRATCH_DIR / "rapport_presentation_ms1_sommaire_tree.json"),
    ("RP_PREAMBULE.pdf", RP_DIR / "RP_PREAMBULE.pdf", SCRATCH_DIR / "rp_preambule_sommaire_tree.json"),
]


def flatten(tree_json):
    flat = []

    def walk(n):
        flat.append((n.get("numero"), n.get("titre"), n.get("page_debut"), n.get("niveau")))
        for c in n.get("enfants", []):
            walk(c)

    for n in tree_json.get("arbre", []):
        walk(n)
    return flat


print("#" * 100)
print("# PART 1 — RP_20251017_MC1_HOTEL_DIEU.pdf (target of Fix 1)")
print("#" * 100)
print()
hd_path = RP_DIR / "RP_20251017_MC1_HOTEL_DIEU.pdf"
hd_out = SCRATCH_DIR / "rp_20251017_mc1_hotel_dieu_sommaire_tree.json"
output, flat_order, doc, col_report = process(hd_path, out_path=hd_out)
print(f"sommaire_pages_detected: {output['sommaire_pages_detected']}")
print(f"n_entries: {len(flat_order)}")
print(f"anchor: {output['anchor_check']['match_rate']}")
print(f"levels: {tree_level_summary(flat_order)}")
print(f"n_warnings: {len(output['warnings'])}")
print()
print("entries:")
for e in flat_order:
    print(f"  niveau={e.niveau} numero={e.numero!r:10} page={e.page_debut:4} titre={e.titre[:70]!r}")
print()
print("warnings:")
for w in output["warnings"]:
    print("  -", w)
print()

print("#" * 100)
print("# PART 2 — non-regression: every OTHER doc must be byte-identical")
print("#" * 100)
print()

any_diff = False
for name, path, saved_path in OTHER_DOCS:
    old = json.loads(saved_path.read_text(encoding="utf-8"))
    new_output, flat_order2, doc2, col_report2 = process(path, out_path=None)
    old_flat = flatten(old)
    new_flat = flatten(new_output)
    old_w, new_w = old["warnings"], new_output["warnings"]
    identical = old_flat == new_flat and old_w == new_w
    status = "IDENTICAL" if identical else "*** DIFFERS ***"
    print(f"{name:32} entries old={len(old_flat)} new={len(new_flat)}  "
          f"anchor old={old['anchor_check']['match_rate']} new={new_output['anchor_check']['match_rate']}  "
          f"warnings old={len(old_w)} new={len(new_w)}  {status}")
    if not identical:
        any_diff = True
        if old_flat != new_flat:
            print("  CONTENT DIFF:")
            n = max(len(old_flat), len(new_flat))
            for i in range(n):
                o = old_flat[i] if i < len(old_flat) else None
                nn = new_flat[i] if i < len(new_flat) else None
                if o != nn:
                    print(f"    [{i}] old={o!r}\n         new={nn!r}")
        if old_w != new_w:
            print("  WARNINGS DIFF:")
            for w in new_w:
                if w not in old_w:
                    print("    NEW +:", w)
            for w in old_w:
                if w not in new_w:
                    print("    OLD -:", w)

print()
print("=" * 100)
print("NON-REGRESSION: " + ("*** SOME DOCS DIFFER — SEE ABOVE ***" if any_diff else "ALL CLEAN — every other doc byte-identical"))
print("=" * 100)
