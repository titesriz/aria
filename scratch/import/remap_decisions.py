"""Re-key a decisions export onto the CURRENT sommaire tree's index paths.

Decisions are matched by CONTENT (numero, titre, page_debut), never by path:
a level change upstream re-nests nodes and silently shifts every index path
beneath them (REG1: 4 "Caractère de la zone …" headings moved niveau 3 -> 2,
misaligning 75/161 keys). Refuses to write if the content mapping is not 1:1.

Usage: remap_decisions.py <tree.json> <old_decisions.json> <doc_key> <out.json>
"""
import collections, json, sys

tree_f, old_f, doc_key, out_f = sys.argv[1:5]
tree = json.load(open(tree_f, encoding="utf-8"))
old = json.load(open(old_f, encoding="utf-8"))
key = lambda n: (n.get("numero"), n.get("titre"), n.get("page_debut"))

by_content = collections.defaultdict(list)
for v in old["decisions"].values():
    by_content[key(v)].append(v)
dupes = [k for k, v in by_content.items() if len(v) > 1]
if dupes:
    sys.exit(f"ambiguous content keys in old decisions: {dupes[:3]}")

new, unmatched, used = {}, [], set()
def walk(nodes, prefix=""):
    for i, n in enumerate(nodes):
        path = f"{prefix}{i}" if prefix == "" else f"{prefix}.{i}"
        src = by_content.get(key(n))
        if src:
            used.add(key(n))
            d = {f: src[0][f] for f in ("choice", "note") if f in src[0]}
            d.update(numero=n["numero"], titre=n["titre"], page_debut=n["page_debut"],
                     page_fin=n["page_fin"], niveau=n["niveau"])
            new[f"{doc_key}::{path}"] = d
        else:
            unmatched.append((path, key(n)))
        walk(n["enfants"], path)
walk(tree["arbre"])

orphans = [k for k in by_content if k not in used]
print(f"tree nodes matched: {len(new)}  unmatched (no decision -> keep): {len(unmatched)}  "
      f"old decisions with no node: {len(orphans)}")
for p, k in unmatched: print("  UNMATCHED", p, k)
for k in orphans: print("  ORPHAN", k)
if orphans:
    sys.exit("refusing to write: some old decisions match no current node")
json.dump({"exported_at": old.get("exported_at"), "remapped_from": old_f.split("/")[-1],
           "decisions": new}, open(out_f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("choices:", dict(collections.Counter(v["choice"] for v in new.values())), "->", out_f)
