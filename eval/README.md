# ARIA RAG — Module d'évaluation

Mesure la qualité du pipeline RAG sur un golden dataset de use cases PLU bioclimatique de Paris.

## Prérequis

```bash
source .venv/bin/activate
pip install -e ".[dev]"

# L'index doit être construit
aria-rag ingest
```

Ollama doit être lancé localement si vous utilisez le backend par défaut :

```bash
ollama serve          # dans un terminal séparé
ollama pull gemma3:4b # si le modèle n'est pas encore téléchargé
```

## Lancer l'évaluation

```bash
# Évaluation complète — tous les use cases, backend Ollama
aria-rag eval

# Cibler des use cases spécifiques
aria-rag eval --ids UC-01 UC-03

# Changer de backend
aria-rag eval --backend openai    # nécessite OPENAI_API_KEY dans .env
aria-rag eval --backend claude    # nécessite ANTHROPIC_API_KEY dans .env

# Ajuster le nombre de chunks récupérés
aria-rag eval --top-k 12

# Dataset et dossier de résultats personnalisés
aria-rag eval --dataset /chemin/vers/mon_dataset.json --output /chemin/vers/resultats/

# Timeout par question (défaut : 120s — utile si Ollama est lent)
aria-rag eval --timeout 180

# Utilisation directe via Python
python eval/run_eval.py --backend ollama --ids UC-01
```

## Structure des fichiers

```
eval/
  golden_dataset.json     — use cases de référence
  run_eval.py             — point d'entrée Python direct
  results/
    results_20260507_143200.json   — résultats horodatés
```

## Golden dataset — use cases couverts

| ID    | Thème                                      | Complexité |
|-------|--------------------------------------------|------------|
| UC-01 | Hauteur max zone UG, secteur DG5           | Moyenne    |
| UC-02 | Retrait voie publique, zone UG             | Moyenne    |
| UC-03 | Prospect limite séparative avec baies PP   | Élevée     |
| UC-04 | Changement destination bureaux → hôtel     | Élevée     |
| UC-05 | Mixité fonctionnelle bureaux + logements   | Élevée     |
| UC-16 | Surélévation bâtiment existant R+4 / DG5   | Élevée     |

## Interpréter les scores

Deux métriques sont calculées pour chaque use case :

### Chunk Retrieval Score
Proportion des articles PLU attendus (ex: `UG.3.2`, `DG5`) trouvés dans les passages récupérés par le pipeline.

```
score = articles_trouvés / articles_attendus
```

Un score faible indique que le pipeline ne remonte pas les bons chunks — problème d'embedding, de BM25, ou de filtre `--family`.

### Answer Coverage Score
Proportion des mots-clés attendus trouvés dans la réponse LLM synthétisée.

```
score = keywords_trouvés / keywords_attendus
```

Un score faible malgré un bon Retrieval Score indique que le LLM ignore ou reformule trop les informations récupérées.

### Interprétation des couleurs

| Couleur | Score  | Signification                          |
|---------|--------|----------------------------------------|
| 🟢 Vert | ≥ 80%  | Bon résultat                           |
| 🟡 Jaune | 50–79% | Résultat partiel, à investiguer        |
| 🔴 Rouge | < 50%  | Échec — chunks ou réponse insuffisants |

## Format des résultats JSON

```json
[
  {
    "id": "UC-01",
    "question": "...",
    "complexity": "Moyenne",
    "retrieval_score": 0.75,
    "answer_score": 0.6,
    "missing_articles": ["DG5"],
    "missing_keywords": ["plan des hauteurs"],
    "raw_passages": "...",
    "raw_answer": "...",
    "error": null
  }
]
```

## Ajouter un use case

Ajouter une entrée dans `eval/golden_dataset.json` :

```json
{
  "id": "UC-XX",
  "question": "Je cherche ...",
  "expected_articles": ["UG.X.X"],
  "expected_keywords": ["mot1", "mot2"],
  "expected_answer_summary": "Résumé de la bonne réponse.",
  "family": "reglement_ecrit",
  "complexity": "Moyenne"
}
```

Le champ `family` correspond aux valeurs acceptées par `--family` : `reglement_ecrit`, `reglement_graphique`, `rapport_presentation`, `oap`, `padd`, `annexes`, `other`.
