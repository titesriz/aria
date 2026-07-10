# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

ARIA is a local RAG pipeline over the **PLU bioclimatique de Paris** PDF corpus. The primary use case is answering urban planning questions (zoning rules, setbacks, heights, mixed-use programmes) grounded in official regulatory documents.

## Common commands

```bash
# Activate the venv first
source .venv/bin/activate

# Install / reinstall in editable mode
pip install -e ".[dev]"

# Ingest PDFs (incremental by default — reuses unchanged files)
aria-rag ingest
aria-rag ingest --max-files 5        # quick test
aria-rag ingest --rebuild            # force full rebuild

# Query — retrieval only (no API cost)
aria-rag ask "question" --no-llm --top-k 8 --family reglement_ecrit

# Query — with LLM synthesis
aria-rag ask "question" --backend openai   # needs OPENAI_API_KEY in .env
aria-rag ask "question" --backend ollama   # needs Ollama running locally

# Patch doc_family/norm_level/city without re-embedding (after corpus_mapping.yaml changes)
python scripts/patch_doc_family.py

# Run ingestion invariant checks (also runs automatically after `aria-rag ingest`)
aria-rag check
aria-rag check --strict   # exit non-zero on any FAIL, for CI

# Run tests
pytest
```

## Architecture

The pipeline has two phases: **ingest** and **ask**.

**Ingest** (`indexer.py`):
1. `loader.py` — walks `Ressources/` recursively, extracts text from PDFs with `pypdf`
2. `corpus_mapping.yaml` (repo root, loaded via `aria_rag.corpus_mapping`) classifies every discovered file by longest-matching folder/file prefix into `{family, norm_level, city, validity}` — config-driven, replaces the old hardcoded `DOC_FAMILIES` dict. A path matching no rule gets `family="other"` (flagged by `aria-rag check`'s family-coverage invariant, not silently accepted). A file with `validity: superseded` (e.g. `REG1.pdf`, byte-identical to the legally-current `REG1_MS1.pdf` consolidation — see the MS1-vs-base audit) is discovered but never indexed.
3. Text is chunked (default 1200 chars / 200 overlap) and filtered by `min_alpha_ratio` to drop scanned/garbage pages
4. Embeddings are built with `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` and stored in a FAISS `IndexFlatIP` (cosine similarity on normalized vectors)
5. A BM25Okapi index is also built and pickled alongside
6. A manifest tracks file size + mtime for incremental re-ingestion
7. `aria-rag check` runs automatically after ingest — see "Ingestion invariants" below

**Ask** (`retriever.py`):
1. Query is encoded with the same embedding model
2. Both FAISS (semantic) and BM25 (lexical) are searched independently with `fetch_k = limit × 10`
3. Results are fused with **Reciprocal Rank Fusion** (RRF_K=60)
4. Optional `--family` filter restricts the candidate set before both searches
5. Top-k hits are passed to `llm.py` as context for answer synthesis

**LLM** (`llm.py`): supports OpenAI (`gpt-4.1-mini` default) and Ollama (`gemma3:4b` default). The system prompt instructs the model to answer only from provided context.

## Corpus structure

Classification is config-driven (`corpus_mapping.yaml`, repo root) — see that file for the authoritative, versioned rule list. Today's tree:

```
Ressources/PLU/75 Paris/PLU Bioclimatique/
  Règlement/Pièces écrites/     → reglement_ecrit, norm_level=local, city=paris
  Règlement/Documents graphiques/ → reglement_graphique, norm_level=local, city=paris
  Rapport de présentation/      → rapport_presentation, norm_level=local, city=paris
  OAP/                          → oap, norm_level=local, city=paris
  PADD/                         → padd, norm_level=local, city=paris
  Annexes/                      → annexes, norm_level=local, city=paris
Ressources/LEGIFRANCE/CCH/       → cch, norm_level=national, city=null
```

`REG1.pdf` and `REG2A1.pdf` are `validity: superseded` — byte-identical to `REG1_MS1.pdf` / `REG2A1_MS1.pdf` (verified by exhaustive page-by-page text diff), the legally-current "modification simplifiée n°1" consolidations. They're discovered by the file walk but never chunked/indexed, so citations for this content correctly show the `_MS1` filename.

A file matching no rule gets `doc_family = "other"` — `aria-rag check`'s family-coverage invariant treats any `other` chunk as a hard failure (nothing should legitimately land there; every current source is mapped).

`cch` currently has no scoped-retrieval slots (`config.py`'s `DEFAULT_FAMILY_SLOTS`) — documented as intentionally unserved in `aria_rag.check.KNOWN_UNSERVED_FAMILIES` pending a later stage of the CCH dual-source prototype that gives it real slots.

## Known issues / gotchas

- **macOS NFD encoding**: folder names with accented characters (é, è, î…) are stored as NFD by HFS+. `aria_rag.corpus_mapping.classify_path()` normalizes paths to NFC before matching — this must be preserved whenever `corpus_mapping.yaml` prefixes are edited.
- **Stale index**: if `Chunk` dataclass fields change, existing `chunks.json` will fail to deserialize. Run `aria-rag ingest --rebuild` or use `scripts/patch_doc_family.py` for lightweight fixes that don't require re-embedding.
- **FAISS k=0 guard**: `retriever.py` returns `[]` early if the family filter matches no chunks, avoiding a FAISS assertion error.
- **HF_TOKEN warning**: the sentence-transformers model loads from cache; the unauthenticated HF Hub warning is harmless.
