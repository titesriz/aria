"""One-off: joins the Notion "PLU Paris - ontologie documentaire" catalog
(scratch/notion_docs_all.jsonl, 411 document rows fetched via view-mode
pagination) against the Famille relation lookup table
(scratch/notion_family_lookup.json, 12 rows) to populate a human-readable
family name, and writes eval/corpus_metadata.json.

Context: eval/corpus_metadata.json's "family" field was empty because it was
carrying raw unresolved Notion relation URLs (e.g.
"https://app.notion.com/p/39f3ae1f2a6d81528647f975e5a27ed4") instead of a
name. This script resolves each row's Famille URL against the lookup table.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
lookup = json.loads((ROOT / "scratch" / "notion_family_lookup.json").read_text(encoding="utf-8"))

# Match on the trailing 32-hex-char page id only -- the lookup table's keys
# and the document rows' Famille URLs disagree on whether a "/p/" segment
# precedes the id, so a full-URL match is too fragile.
def normalize(url: str) -> str:
    return url.rstrip("/").split("?")[0].split("/")[-1]

lookup_norm = {normalize(k): v for k, v in lookup.items()}

rows = []
with (ROOT / "scratch" / "notion_docs_all.jsonl").open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))

unresolved = []
out_rows = []
for row in rows:
    famille_urls = row.get("Famille") or []
    # Some pages were extracted from a raw tool-result JSON where "Famille"
    # came through as a JSON-encoded string (e.g. '["https://..."]') rather
    # than an actual list -- parse it if so, instead of iterating characters.
    if isinstance(famille_urls, str):
        famille_urls = json.loads(famille_urls) if famille_urls else []
    resolved = []
    for u in famille_urls:
        nu = normalize(u)
        if nu in lookup_norm:
            resolved.append(lookup_norm[nu])
        else:
            unresolved.append((row.get("PDF"), u))
    out_rows.append({
        "url": row.get("url"),
        "pdf": row.get("PDF"),
        "chemin_relatif": row.get("Chemin relatif"),
        "family": resolved[0]["famille"] if resolved else None,
        "family_ressource": resolved[0]["ressource"] if resolved else None,
        "structure_native": row.get("Structure native"),
        "indexable_texte": row.get("Indexable texte") == "oui",
    })

out_rows.sort(key=lambda r: (r["family"] or "", r["chemin_relatif"] or ""))

out_path = ROOT / "eval" / "corpus_metadata.json"
out_path.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"wrote {len(out_rows)} rows to {out_path}")
print(f"unresolved Famille URLs: {len(unresolved)}")
for pdf, u in unresolved[:20]:
    print("  ", pdf, u)

empty_family = [r for r in out_rows if not r["family"]]
print(f"rows with empty family after resolution: {len(empty_family)}")
for r in empty_family[:20]:
    print("  ", r["pdf"], r["chemin_relatif"])

from collections import Counter
counts = Counter(r["family"] for r in out_rows)
print("\nfamily counts:")
for fam, cnt in counts.most_common():
    print(f"  {fam}: {cnt}")
