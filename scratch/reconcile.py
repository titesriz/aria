"""
Reconcile PDFs physically on disk under the PLU corpus root against the
412 retained rows in eval/ontology/corpus_pdf_inventory_full.csv.

Scratch-only, read-only against the existing project files. Writes
scratch/corpus_reconciliation.csv and prints a console summary.
"""
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("Ressources/PLU/75 Paris/PLU Bioclimatique")
CSV_PATH = Path("eval/ontology/corpus_pdf_inventory_full.csv")
OUT_PATH = Path("scratch/corpus_reconciliation.csv")

# ---------------------------------------------------------------------------
# 1. Walk disk
# ---------------------------------------------------------------------------
disk_paths = []
for p in ROOT.rglob("*.pdf"):
    if p.is_file():
        rel = p.relative_to(ROOT).as_posix()
        disk_paths.append(rel)

disk_counts = Counter(disk_paths)
disk_set = set(disk_counts)

# ---------------------------------------------------------------------------
# 2. Load CSV retained paths
# ---------------------------------------------------------------------------
csv_paths = []
with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rel = row["chemin_relatif"].strip().replace("\\", "/")
        csv_paths.append(rel)

csv_counts = Counter(csv_paths)
csv_set = set(csv_counts)

# ---------------------------------------------------------------------------
# 3. Compute discrepancy sets
# ---------------------------------------------------------------------------
excluded = sorted(disk_set - csv_set)          # on disk, not in CSV
phantom = sorted(csv_set - disk_set)            # in CSV, not on disk
disk_dupes = sorted(p for p, n in disk_counts.items() if n > 1)
csv_dupes = sorted(p for p, n in csv_counts.items() if n > 1)

# ---------------------------------------------------------------------------
# 4. Inference rules for excluded files
# ---------------------------------------------------------------------------
VARIANT_SUFFIX_RE = re.compile(
    r"(_MS\d*|_MC\d*|_DEx?\d*|_\d{4}(_\d{2}(_\d{2})?)?)(?=\.[Pp][Dd][Ff]$)"
)


def strip_variant_suffix(filename: str) -> str:
    stem, dot, ext = filename.rpartition(".")
    stem2 = VARIANT_SUFFIX_RE.sub("", filename)
    stem2 = stem2[: -(len(ext) + 1)] if stem2.endswith("." + ext) else stem2
    return stem2


def infer_reason(rel_path: str) -> str:
    parts = rel_path.split("/")
    top_folder = parts[0] if parts else ""
    filename = parts[-1]

    if top_folder == "Anciens PLU":
        return "ancien PLU (hors périmètre)"

    if "Tome 2" in parts and "Pièces écrites" in parts:
        return "règlement tome 2"

    # variant/version suffix of a file already retained (compare stems
    # within the same directory, since filenames are not globally unique)
    m = VARIANT_SUFFIX_RE.search(filename)
    if m:
        base_filename = filename[: m.start()] + filename[filename.rfind(".") :]
        sibling_dir = "/".join(parts[:-1])
        base_rel = f"{sibling_dir}/{base_filename}" if sibling_dir else base_filename
        if base_rel in csv_set or base_rel in disk_set:
            return "variante de version"

    return "à qualifier"


# ---------------------------------------------------------------------------
# 5. Write output CSV
# ---------------------------------------------------------------------------
rows = []

for rel in excluded:
    parts = rel.split("/")
    rows.append(
        {
            "relative_path": rel,
            "filename": parts[-1],
            "top_folder": parts[0] if parts else "",
            "status": "excluded",
            "inferred_reason": infer_reason(rel),
        }
    )

for rel in phantom:
    parts = rel.split("/")
    rows.append(
        {
            "relative_path": rel,
            "filename": parts[-1],
            "top_folder": parts[0] if parts else "",
            "status": "phantom_in_csv",
            "inferred_reason": "",
        }
    )

for rel in disk_dupes:
    parts = rel.split("/")
    rows.append(
        {
            "relative_path": rel,
            "filename": parts[-1],
            "top_folder": parts[0] if parts else "",
            "status": "duplicate",
            "inferred_reason": f"on-disk duplicate x{disk_counts[rel]}",
        }
    )

for rel in csv_dupes:
    parts = rel.split("/")
    rows.append(
        {
            "relative_path": rel,
            "filename": parts[-1],
            "top_folder": parts[0] if parts else "",
            "status": "duplicate",
            "inferred_reason": f"CSV duplicate row x{csv_counts[rel]}",
        }
    )

with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["relative_path", "filename", "top_folder", "status", "inferred_reason"],
    )
    writer.writeheader()
    writer.writerows(rows)

# ---------------------------------------------------------------------------
# 6. Console summary
# ---------------------------------------------------------------------------
reason_counts = Counter(infer_reason(rel) for rel in excluded)

print(f"On-disk PDFs (unique paths): {len(disk_set)} (raw file count: {len(disk_paths)})")
print(f"CSV retained rows (unique paths): {len(csv_set)} (raw row count: {len(csv_paths)})")
print(f"Excluded (on disk, not in CSV): {len(excluded)}")
for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
    print(f"  - {reason}: {count}")
print(f"Phantom (in CSV, not on disk): {len(phantom)}")
print(f"On-disk duplicate paths: {len(disk_dupes)}")
print(f"CSV duplicate rows: {len(csv_dupes)}")
print(f"\nWrote {OUT_PATH} ({len(rows)} rows)")

print("\n=== Excluded files grouped by inferred_reason ===")
by_reason = defaultdict(list)
for rel in excluded:
    by_reason[infer_reason(rel)].append(rel)

for reason in sorted(by_reason, key=lambda r: -len(by_reason[r])):
    print(f"\n-- {reason} ({len(by_reason[reason])}) --")
    for rel in by_reason[reason]:
        print(f"  {rel}")

if phantom:
    print("\n=== Phantom CSV entries (not found on disk) ===")
    for rel in phantom:
        print(f"  {rel}")
