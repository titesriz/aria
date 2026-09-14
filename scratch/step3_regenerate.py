"""STEP 3 — regenerate every document's sommaire tree with the fully
corrected canonical engine, overwriting each doc's saved
*_sommaire_tree.json in place, and report a per-document diff.

Baseline for the diff is the git-HEAD-committed tree (7b3d132) — the state
from BEFORE this session's fixes (Step 1 zone-code, Step 2 DIAGNOSTIC split)
AND before the PRIOR session's fixes (column-split robustness, PDF-wrap
dehyphenation, generic "Tableau N." numbering profile, niveau<1 guard,
duplicate-code detection — the "earlier relative-level consolidation" the
task's own EXPECTED-change taxonomy names as a distinct bucket). Using
git HEAD as "old" is deliberate: it lets every change be classified against
the SAME 3-bucket taxonomy the task specifies, in one pass, rather than
against an intermediate on-disk state that was already overwritten and no
longer exists to diff against.

Diff is PROPERLY ALIGNED (difflib SequenceMatcher on (numero, titre,
page_debut), not raw index) — a naive positional zip misclassifies every
entry after an entry-count change as "changed" just because the list
shifted by one position.

Nothing written to Notion. Run: python3 scratch/step3_regenerate.py
"""
from __future__ import annotations
import difflib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_sommaire import REPO_ROOT, SCRATCH_DIR, process  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

RES = REPO_ROOT / "Ressources" / "PLU" / "75 Paris" / "PLU Bioclimatique"
RP_DIR = RES / "Rapport de présentation"

DOCS = [
    ("REG1_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 1" / "REG1_MS1.pdf",
     SCRATCH_DIR / "reg1_sommaire_tree.json"),
    ("REG2A1_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A1_MS1.pdf",
     SCRATCH_DIR / "reg2a1_sommaire_tree.json"),
    ("REG2A10_1DE2_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A10_1DE2_MS1.pdf",
     SCRATCH_DIR / "reg2a10_1de2_ms1_sommaire_tree.json"),
    ("REG2A10_2DE2_MS1.pdf", RES / "Règlement" / "Pièces écrites" / "Tome 2" / "REG2A10_2DE2_MS1.pdf",
     SCRATCH_DIR / "reg2a10_2de2_ms1_sommaire_tree.json"),
]


def git_head_json(path: Path):
    rel = path.relative_to(REPO_ROOT).as_posix()
    try:
        raw = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=REPO_ROOT, capture_output=True, check=True
        ).stdout.decode("utf-8")
    except subprocess.CalledProcessError:
        return None
    return json.loads(raw)


def flatten(tree_json):
    flat = []

    def walk(n):
        flat.append((n.get("numero"), n.get("titre"), n.get("page_debut"), n.get("niveau")))
        for c in n.get("enfants", []):
            walk(c)

    for n in tree_json.get("arbre", []):
        walk(n)
    return flat


def _numero_upgrade_pattern(o, n):
    """True if numero went from a shorter/absent code to a longer one that
    starts with it (zone-code full-code extraction, Step 1) OR from None to
    a real code (Tableau-profile recognition, prior session) — in both
    cases the title correspondingly shrinks (the code text that moved out
    of the title into numero) and the page is unchanged."""
    o_num, o_title, o_page = o[0], o[1], o[2]
    n_num, n_title, n_page = n[0], n[1], n[2]
    if o_page != n_page or not n_num or not o_title or not n_title:
        return False
    if not o_title.endswith(n_title):
        return False
    if o_num is None:
        return True  # None -> real code, title shrunk by the same amount
    return n_num.startswith(o_num) and n_num != o_num


def classify(old_flat, new_flat):
    old_content = [(o[0], o[1], o[2]) for o in old_flat]
    new_content = [(n[0], n[1], n[2]) for n in new_flat]
    sm = difflib.SequenceMatcher(a=old_content, b=new_content, autojunk=False)
    expected, unexpected = [], []
    raw_deletes, raw_inserts = [], []  # for the global-reordering pass below

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for oi, ni in zip(range(i1, i2), range(j1, j2)):
                if old_flat[oi][3] != new_flat[ni][3]:
                    expected.append((oi, old_flat[oi], new_flat[ni], "level-only (relative-level recompute)"))
        elif tag == "replace":
            old_slice, new_slice = old_flat[i1:i2], new_flat[j1:j2]
            if len(old_slice) == len(new_slice) and all(_numero_upgrade_pattern(o, n) for o, n in zip(old_slice, new_slice)):
                for o, n in zip(old_slice, new_slice):
                    reason = "zone-code full-code extraction (Step 1, this session)" if o[0] else "numbering profile now recognized, e.g. 'Tableau N.' (prior session)"
                    expected.append((i1, o, n, reason))
            elif len(old_slice) == 1 and len(new_slice) > 1 and old_slice[0][1] and all(n[1] and n[1] in old_slice[0][1] for n in new_slice) and new_slice[-1][2] == old_slice[0][2]:
                expected.append((i1, old_slice[0], new_slice, "entry split from a fused/contaminated title (Step 2, this session, or prior-session fixes)"))
            elif len(old_slice) > 1 and len(new_slice) == 1 and new_slice[0][1] and all(o[1] and o[1] in new_slice[0][1] for o in old_slice) and old_slice[-1][2] == new_slice[0][2]:
                expected.append((i1, old_slice, new_slice[0], "entries merged (prior-session fix)"))
            else:
                raw_deletes.append((i1, old_slice))
                raw_inserts.append((i1, new_slice))
        elif tag == "delete":
            raw_deletes.append((i1, old_flat[i1:i2]))
        elif tag == "insert":
            raw_inserts.append((i1, new_flat[j1:j2]))

    # Global reordering pass: if the MULTISET of (numero, titre, page) across
    # all otherwise-unclassified deletes equals the multiset across all
    # otherwise-unclassified inserts, the whole set is a REORDER (same
    # entries, different position — a linear diff always shows a moved block
    # as delete-here + insert-there, so this must be checked globally, not
    # per-opcode) — the prior session's column-split-order fix does exactly
    # this on RP_RNT.pdf. Level is deliberately excluded from the key: a
    # reordered entry's level legitimately differs (it's RELATIVE to
    # position — see compute_relative_levels), so requiring level equality
    # here would wrongly reject a genuine reorder.
    del_key = sorted((o[0], o[1], o[2]) for _, block in raw_deletes for o in block)
    ins_key = sorted((n[0], n[1], n[2]) for _, block in raw_inserts for n in block)
    if raw_deletes and del_key == ins_key:
        for i1, block in raw_deletes:
            for o in block:
                expected.append((i1, o, None, "reordering, same content different position (prior-session column-split fix)"))
        for i1, block in raw_inserts:
            for n in block:
                expected.append((i1, None, n, "reordering, same content different position (prior-session column-split fix)"))
        return expected, unexpected

    # Fallback: a block can be BOTH reordered AND numero-upgraded at once
    # (RP_INDIC.pdf: the "Tableau N." list moved position AND had its code
    # extracted from the title in the same pass). Match leftover deleted
    # entries to leftover inserted entries by page_debut (invariant across
    # both transforms) + the same title-substring relation used above,
    # regardless of position.
    del_items = [o for _, block in raw_deletes for o in block]
    ins_items = [n for _, block in raw_inserts for n in block]
    matched_ins = set()
    still_unmatched_del = []
    for o in del_items:
        match = None
        for idx, n in enumerate(ins_items):
            if idx in matched_ins or n[2] != o[2]:
                continue
            if _numero_upgrade_pattern(o, n) or (o[1] and n[1] and (o[1] in n[1] or n[1] in o[1])):
                match = idx
                break
        if match is not None:
            matched_ins.add(match)
            expected.append((0, o, ins_items[match], "reordering + numbering-profile upgrade combined (prior-session fixes)"))
        else:
            still_unmatched_del.append(o)
    still_unmatched_ins = [n for idx, n in enumerate(ins_items) if idx not in matched_ins]

    for o in still_unmatched_del:
        unexpected.append((0, o, None, "delete, NOT explained by any known pattern or a global reorder"))
    for n in still_unmatched_ins:
        unexpected.append((0, None, n, "insert, NOT explained by any known pattern or a global reorder"))

    return expected, unexpected


def report_doc(name, old, new_output, all_unexpected):
    if old is None:
        print(f"--- {name}: no HEAD baseline (new file) ---")
        return
    old_flat, new_flat = flatten(old), flatten(new_output)
    if old_flat == new_flat:
        print(f"--- {name}: byte-identical to HEAD (0 changes) ---")
        return
    expected, unexpected = classify(old_flat, new_flat)
    print(f"--- {name} ---")
    print(f"  entries: HEAD={len(old_flat)} now={len(new_flat)}  "
          f"anchor: HEAD={old['anchor_check']['match_rate']} now={new_output['anchor_check']['match_rate']}  "
          f"warnings: HEAD={len(old['warnings'])} now={len(new_output['warnings'])}")
    reason_counts = {}
    for _, _, _, reason in expected:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    print(f"  EXPECTED changes: {len(expected)}")
    for reason, cnt in reason_counts.items():
        print(f"    {cnt}x {reason}")
    if unexpected:
        print(f"  *** UNEXPECTED changes: {len(unexpected)} ***")
        for i, o, nw, reason in unexpected:
            print(f"    [{i}] ({reason})\n      old={o}\n      new={nw}")
        all_unexpected[name] = unexpected
    else:
        print("  UNEXPECTED changes: 0")
    print()


all_unexpected = {}
final_anchor = {}

print("#" * 100)
print("# STEP 3 — FULL REGENERATION vs git-HEAD baseline")
print("#" * 100)
print()

for name, path, saved_path in DOCS:
    old = git_head_json(saved_path)
    output, flat_order, doc, col_report = process(path, out_path=saved_path)
    final_anchor[name] = output["anchor_check"]["match_rate"]
    report_doc(name, old, output, all_unexpected)

print("#" * 100)
print("# PART 2 — Rapport de présentation/ folder (regenerate every doc)")
print("#" * 100)
print()

pdfs = sorted(RP_DIR.glob("*.pdf"))
header = f"{'fichier':38} | {'col':>10} | {'entries':>7} | {'anchor':>7} | {'warn':>4}"
print(header)
print("-" * len(header))

rp_new = {}
for pdf_path in pdfs:
    out_path = SCRATCH_DIR / f"{pdf_path.stem.lower()}_sommaire_tree.json"
    output, flat_order, doc, col_report = process(pdf_path, out_path=out_path)
    anchor = output["anchor_check"]["match_rate"]
    final_anchor[pdf_path.name] = anchor
    col_str = "/".join(str(c) for c in col_report) if col_report else "n/a"
    anchor_str = f"{anchor:.0%}" if anchor is not None else "n/a"
    print(f"{pdf_path.name:38} | {col_str:>10} | {len(flat_order):>7} | {anchor_str:>7} | {len(output['warnings']):>4}")
    rp_new[pdf_path.name] = (out_path, output)

print()
print("#" * 100)
print("# PART 2 diff vs git-HEAD baseline")
print("#" * 100)
print()

for name, (out_path, output) in rp_new.items():
    old = git_head_json(out_path)
    report_doc(name, old, output, all_unexpected)

print("#" * 100)
print("# FINAL ANCHOR TABLE (all documents)")
print("#" * 100)
for name, path, saved_path in DOCS:
    print(f"  {name:30} anchor={final_anchor[name]}")
for name in rp_new:
    print(f"  {name:30} anchor={final_anchor[name]}")

print()
print("#" * 100)
print("# TOTAL UNEXPECTED CHANGES ACROSS ALL DOCS")
print("#" * 100)
if not all_unexpected:
    print("NONE — every change in every document is classified EXPECTED.")
else:
    for name, u in all_unexpected.items():
        print(f"  {name}: {len(u)} unexpected changes")
