"""One-off runner for the 12 golden-v2 cases (T5 measurement task) --
retrieval only. LLM synthesis (answer summary, citation) is unavailable
this session: Ollama's /api/generate hangs past 60s for both
expansion (gemma3:4b) and synthesis (ministral-3:8b) models -- confirmed
via a direct curl test, not just the pipeline's own backend_check. Matches
the documented Vulkan/CUDA fix requiring a reboot to persist (see memory).
Not fixed here -- out of scope for a measurement-only task.

Loads the index once and runs all 12 queries in-process (no per-question
subprocess cold-start), unlike the CLI path used in prior sessions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from aria_rag.config import load_settings
from aria_rag.retriever import load_index, search

CASES = [
    ("UC-01", "Quelle est la hauteur maximale constructible sur une parcelle en zone UG à Paris 11e, rue de la Roquette, pour un projet de logements neufs de 800m² de surface de plancher ?", "reglement_ecrit", ["UG.3.2"]),
    ("UC-02", "Ma construction neuve peut-elle être implantée en retrait de 3m par rapport à l'alignement de la voie, en zone UG, programme mixte logements/commerces, Paris 15e ?", "reglement_ecrit", ["UG.3.1.1"]),
    ("UC-03", "Quelle règle de prospect dois-je respecter vis-à-vis de la limite séparative droite, avec des baies de pièces principales à 8m de haut, en zone UG ?", "reglement_ecrit", ["UG.3.1"]),
    ("UC-04", "La création d'un hôtel de 40 chambres est-elle autorisée en zone UG Paris 8e sur une parcelle actuellement en bureaux (600m²), et à quelles conditions ?", "reglement_ecrit", ["UG.1.3"]),
    ("UC-05", "Quelle proportion minimale de surface de plancher doit être affectée au logement dans un programme mixte bureaux+logements de 2000m² en zone UG ?", "reglement_ecrit", ["UG.1.4.1"]),
    ("UC-16", "Puis-je surélever d'un niveau (R+1) mon immeuble R+4 existant à 17m en zone UG Paris 9e, où le plafond de hauteur DG5 est à 18m ?", "reglement_ecrit", ["UG.3.2", "UG.3.3"]),
    ("CH-01", "Je veux faire une surélévation d'un immeuble de bureau à Paris. Quelles sont les restrictions ?", "reglement_ecrit", ["UG.3.2", "UG.3.3", "UG.1.4.1"]),
    ("CH-02", "Je souhaite installer des volets, quelles sont les règles à respecter dans le 10e arrondissement ?", "reglement_ecrit", ["UG.2.2"]),
    ("CH-03", "Je souhaite remplacer mon toit en zinc par un toit en tuile. Est-ce que c'est possible ?", "reglement_ecrit", ["UG.2.2.3", "Annexe X"]),
    ("CH-04", "Donne moi la liste des occupations et utilisations du sol interdites", "reglement_ecrit", ["UG.1.1", "UGSU.1.1", "UV.1.1", "N.1.1"]),
    ("CH-05", "Donne la liste des secteurs soumis à des dispositions particulières", "reglement_ecrit", ["Annexe I"]),
    ("CH-06", "Peux-tu me donner la liste des adresses du 1er arrondissement (emplacements réservés logement) ?", "reglement_ecrit", ["Annexe V"]),
]

TOP_K = 10


def normalize(s):
    import re
    return re.sub(r"\s+", "", s or "").lower().rstrip(".")


def hit_satisfies(section, value):
    if section is None:
        return False
    return normalize(value) in normalize(section)


def main():
    settings = load_settings()
    print(f"[config] top_k={TOP_K} expand_query=False scoped_retrieval={settings.scoped_retrieval} "
          f"family_slots={settings.family_slots}", flush=True)
    model, faiss_index, bm25, chunks = load_index(settings)
    print(f"[config] index loaded: {len(chunks)} chunks", flush=True)

    results = []
    for case_id, question, expected_family, expected_articles in CASES:
        hits = search(settings, question, top_k=TOP_K, debug=False)
        hit_rows = [
            {"source": Path(h.source_path).name, "family": h.doc_family, "section": h.section, "score": h.score}
            for h in hits
        ]
        family_hit_count = sum(1 for h in hits if h.doc_family == expected_family)
        family_ok = family_hit_count >= 1
        matched_articles = [
            a for a in expected_articles
            if any(hit_satisfies(h.section, a) for h in hits)
        ]
        article_ok = len(matched_articles) == len(expected_articles)
        article_partial = len(matched_articles) > 0

        results.append({
            "case_id": case_id,
            "question": question,
            "expected_family": expected_family,
            "expected_articles": expected_articles,
            "matched_articles": matched_articles,
            "family_hit_count": family_hit_count,
            "family_ok": family_ok,
            "article_ok": article_ok,
            "article_partial": article_partial,
            "hits": hit_rows,
        })
        print(f"[{case_id}] family_ok={family_ok} ({family_hit_count}/{TOP_K} {expected_family}) "
              f"article_ok={article_ok} matched={matched_articles}/{expected_articles}", flush=True)

    # CH-06 deep dive: how many Annexe V "1er" rows surface at higher top_k
    print("\n[CH-06 deep dive] Annexe V rows for '1er' at increasing top_k, family-scoped", flush=True)
    import re
    for k in (10, 20, 50, 100):
        hits = search(settings, CASES[-1][1], top_k=k, family_filter=["reglement_ecrit"], debug=False)
        annexe_v_1er = [
            h for h in hits
            if h.section == "Annexe V" and re.search(r"(?m)^1er\b", h.content or "")
        ]
        print(f"  top_k={k}: {len(annexe_v_1er)} Annexe-V '1er' rows in results "
              f"({sum(1 for h in hits if h.section == 'Annexe V')} total Annexe V hits)", flush=True)

    out_path = Path(__file__).resolve().parents[1] / "eval" / "results" / "golden_v2_retrieval_20260720.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
