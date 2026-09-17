"""Build the section-injection plan for the Notion "inventaire corpus" database.

Walks a sommaire tree (scratch/<doc>_sommaire_tree.json), applies Charline's
review decisions (scratch/import/decisions_<DOC>.json), and emits the kept
sections with their hierarchical chemin_relatif and parent resolution.

Decision rule:
- choice == "drop" -> node excluded; its children re-attach (see below).
- anything else (note-only, or no decision) -> kept.

Re-attachment rule for children of dropped nodes (the RP_CHOIX lesson,
2026-09-16): a dropped node's child re-attaches to the KEPT section whose
numero is the dot-prefix of the child's numero (e.g. "1.5.1" -> "1.5"),
searched among kept nodes sharing the same nearest-kept-ancestor scope AND
sitting exactly one niveau above the child (numbering namespaces restart per
partie in these documents — "1.8" under the dropped "Rappel" must NOT match
the unrelated section "1" from page 5). Only when no such match exists does
it fall back to the nearest kept ancestor (and ultimately the document page). Without this, sections like 1.5.1 land next to 1.5 instead
of under it whenever an unnumbered intermediate heading was dropped.

Usage: python3 build_injection_plan.py <tree.json> <decisions.json> <base_chemin> <out_plan.json>
  base_chemin e.g. "Rapport de présentation/RP_CHOIX.pdf"
"""
import json
import re
import sys
import collections


def clean_title(t):
    """Strip TOC-extraction artifacts: control chars (\\x07 bullets, \\x08),
    replacement chars, and trailing dot-leader runs (". . . ." fills)."""
    t = re.sub(r"[\x00-\x1f�]", "", t)
    t = re.sub(r"[.\s]{4,}$", "", t)
    return t.strip()


def seg(n):
    return n["numero"] if n.get("numero") else n["titre"]


def numero_prefix(numero):
    """"1.5.1" -> "1.5"; "Axe 1", "1", None -> None (no dot-prefix)."""
    if not numero or "." not in numero:
        return None
    return numero.rsplit(".", 1)[0]


def build_plan(tree_path, dec_path, base):
    tree = json.load(open(tree_path))
    doc_key = json.load(open(dec_path))["document"].rsplit(".", 1)[0].upper()
    decisions = json.load(open(dec_path))["decisions"]

    # Pass 1: collect kept nodes with their nearest-kept-ancestor chain.
    kept = []

    def walk(nodes, prefix, kept_chain):
        for i, n in enumerate(nodes):
            path = f"{prefix}{i}" if prefix == "" else f"{prefix}.{i}"
            n["titre"] = clean_title(n["titre"])
            d = decisions.get(f"{doc_key}::{path}", {})
            if d.get("choice") == "drop":
                walk(n["enfants"], path, kept_chain)
                continue
            kept.append({
                "path": path,
                "numero": n.get("numero"),
                "titre": n["titre"],
                "niveau": n["niveau"],
                "page_debut": n["page_debut"],
                "page_fin": n["page_fin"],
                "note": d.get("note"),
                "libelle": (f'{n["numero"]} — {n["titre"]}' if n.get("numero") else n["titre"]),
                "_ancestor_chain": list(kept_chain),
            })
            walk(n["enfants"], path, kept_chain + [n])

    walk(tree["arbre"], "", [])

    # Pass 2: for orphans (ancestor chain broken by a drop), prefer the kept
    # node whose numero is the dot-prefix of this node's numero AND which
    # precedes it in document order within the same nearest-kept-ancestor scope.
    by_path = {k["path"]: k for k in kept}
    for k in kept:
        pref = numero_prefix(k["numero"])
        # detect orphan: tree path depth > kept-ancestor-chain depth + 1
        is_orphan = k["path"].count(".") > len(k["_ancestor_chain"])
        if is_orphan and pref:
            scope = " > ".join(seg(a) for a in k["_ancestor_chain"])
            candidates = [
                c for c in kept
                if c["numero"] == pref
                and c["niveau"] == k["niveau"] - 1
                and " > ".join(seg(a) for a in c["_ancestor_chain"]) == scope
                and c["path"] < k["path"]
            ]
            if candidates:
                k["_ancestor_chain"] = candidates[-1]["_ancestor_chain"] + [
                    {"numero": candidates[-1]["numero"], "titre": candidates[-1]["titre"]}
                ]

    for k in kept:
        segs = [seg(a) for a in k["_ancestor_chain"]]
        k["chemin_relatif"] = " > ".join([base] + segs + [seg(k)])
        k["parent_chemin"] = " > ".join([base] + segs) if segs else base
        del k["_ancestor_chain"]

    # Chemin collision (e.g. RP_RNT's source sommaire numbers two sections
    # "3.1"): keep the libellé faithful to the source, but disambiguate the
    # later duplicate's chemin segment with its titre and flag it in Note.
    seen = {}
    for k in kept:
        c = k["chemin_relatif"]
        if c in seen:
            k["chemin_relatif"] = f'{k["parent_chemin"]} > {k["numero"]} {k["titre"]}'
            dup_note = f'numero "{k["numero"]}" en doublon dans le sommaire source (coquille probable)'
            k["note"] = f'{k["note"]} — {dup_note}' if k["note"] else dup_note
        seen[c] = True
    chem = [k["chemin_relatif"] for k in kept]
    dupes = [c for c, ct in collections.Counter(chem).items() if ct > 1]
    if dupes:
        raise SystemExit("duplicate chemins:\n  " + "\n  ".join(dupes))
    return kept


if __name__ == "__main__":
    tree_path, dec_path, base, out = sys.argv[1:5]
    kept = build_plan(tree_path, dec_path, base)
    by_level = collections.Counter(k["niveau"] for k in kept)
    print("total kept:", len(kept))
    print("per level:", dict(sorted(by_level.items())))
    print("with notes:", sum(1 for k in kept if k["note"]))
    json.dump(kept, open(out, "w"), ensure_ascii=False, indent=1)
    print("plan written to", out)
