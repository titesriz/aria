# ARIA — Project Map

Reference document for navigating and modifying this codebase — stable structure only (modules, data flow, dependencies). For current/dated state — open bugs, recent fixes, eval numbers, roadmap — see `SESSION_STATE.md` (newest first), not here. Written 2026-09-19, restructured 2026-09-20 to keep that split clean.

---

## 1. Project structure

```
aria/
├── src/aria_rag/          Python package — the entire application (backend + CLI + API). No separate frontend in this repo.
├── tests/                 pytest suite, 17 files, ~236 tests. tests/fixtures/ holds test data.
├── eval/                  Golden-dataset evaluation: datasets, cached results, scoring methodology docs.
├── scripts/               One-off / diagnostic scripts, NOT part of the package. Mostly historical — several say so in their own docstrings; check before trusting one still matches the current schema.
├── checks/                JSON allowlists consumed by aria_rag.check (known, documented corpus quirks).
├── scratch/                Session-local working files — extraction outputs, ad hoc scripts, logs. Not curated, not part of the shipped product.
├── data/
│   ├── index/              Generated: chunks.json, index.faiss, bm25.pkl, manifest.json + check_reports/. Gitignored, machine-local.
│   └── sessions/           Append-only JSONL logs of every /ask API call + /feedback submission (aria_rag.sessions).
├── Ressources/             Source PDF corpus (PLU bioclimatique de Paris + CCH). Gitignored, machine-local. Standing risk: `corpus_mapping.yaml` (git-tracked) encodes an unversioned assumption about this folder's layout — if a checkout's real tree doesn't match it, `classify_path()` silently returns `family="other"` for everything and retrieval breaks with no error. True on every machine as long as `Ressources/`/`data/index/` stay gitignored (they must — the corpus and index are too large/private to track). Verify `aria-rag check`'s check 0 (corpus mapping coverage) before trusting anything else on a new checkout.
├── CLAUDE.md                Project instructions for AI assistants — architecture summary, invariants, conventions. Read this first.
├── SESSION_STATE.md         Dated session log (append-only, newest first) — the actual project history/decision record.
├── corpus_mapping.yaml      Folder-prefix → {family, norm_level, city, validity} classification rules.
├── referentiel.yaml         Piece-level corpus manifest (which files serve which "official document" for the /document API + citations).
├── pyproject.toml           Package metadata, dependencies, the `aria-rag` console-script entry point.
├── README.md                 Quick-start only; this file for depth.
└── .env / .env.example       Runtime config (API keys, model names, thresholds).
```

No `frontend/`, `static/`, `templates/`, or `package.json` anywhere in this repo. The API (`aria_rag.api`) sets `CORS allow_origins=["*"]` — it's built to be consumed by a UI that lives elsewhere, not bundled here.

---

## 2. Entry points

| Entry point | What it does |
|---|---|
| `aria-rag ingest` | Extract PDFs → build/update the FAISS + BM25 index. Reuses extraction + embedding for unchanged files; the FAISS/BM25 index itself is rebuilt in full on every write. `--rebuild --family X` for a scoped rebuild. |
| `aria-rag check` | Run 11 corpus/index invariants (`aria_rag.check`), report PASS/WARN/FAIL. Runs automatically after `ingest` unless `--skip-check`. |
| `aria-rag ask "<question>"` | One-shot CLI query: retrieve + optionally synthesize an answer. `--no-llm` for retrieval-only, `--debug` for per-chunk scores. |
| `aria-rag eval` | Run the golden-dataset evaluation (retrieval + answer scoring). `--no-llm`, `--multi-alpha`, `--expand-query` variants. |
| `aria-rag serve` | Start the FastAPI server on port 8000 (`aria_rag.api:app` via uvicorn). Refuses to start if Ollama models aren't GPU-resident, unless `--allow-cpu`. |
| `aria-rag sessions --last N` | Read-only digest of the last N logged `/ask` calls. |
| `aria-rag referentiel export/import` | Round-trip `referentiel.yaml` ↔ `.xlsx` for domain-expert review. |
| `python -m aria_rag.cli` won't work | The package has no `__main__.py` — always use the installed `aria-rag` console script (`pyproject.toml`'s `[project.scripts]`). |
| `eval/run_eval.py` | Thin CLI wrapper around `aria_rag.eval.run_eval` — same as `aria-rag eval`, standalone-runnable. |

All CLI wiring lives in one file: **`src/aria_rag/cli.py`** (511 lines, `build_parser()` + `main()`). Start here to understand any command's actual arguments.

---

## 3. Core components

| Component | File | Purpose | Key deps |
|---|---|---|---|
| **Config** | `config.py` | `Settings` dataclass + `load_settings()` (env var resolution). `DEFAULT_FAMILY_SLOTS` controls scoped-retrieval quota per family — note `cch` isn't in it at all, so even a fully-ingested CCH corpus would never surface in scoped retrieval until this is added. | `python-dotenv` |
| **Corpus classification** | `corpus_mapping.py` + `corpus_mapping.yaml` | Longest-prefix-match: file path → `{family, norm_level, city, validity}`. Everything downstream (chunking, retrieval slots, checks) keys off `family`. | `pyyaml` |
| **PDF loading** | `loader.py` | pypdf-based text extraction, per-page, with font/whitespace normalization. | `pypdf` |
| **Indexer** | `indexer.py` (~1000 lines, the biggest module) | Chunking (article-header-aware for `reglement_ecrit`, table-row-aware for annexe files, sliding-window fallback elsewhere) + embedding + FAISS/BM25 index build. `build_index()` is the core entry. | `sentence-transformers`, `faiss-cpu`, `rank-bm25` |
| **Retriever** | `retriever.py` (~600 lines) | Hybrid FAISS+BM25 search fused by Reciprocal Rank Fusion, scoped per-family, plus a deterministic "annexe route" for address/list-style queries and a lexical boost for discriminating tokens (arrondissement numbers, article codes). | `faiss`, `sentence-transformers` |
| **Query expansion** | `query_expansion.py` | Ollama call that infers likely PLU article codes from a natural-language question before retrieval; results cached to disk. | Ollama (gemma3:4b) |
| **LLM synthesis** | `llm.py` | Builds the grounding-constrained prompt, dispatches to OpenAI / Ollama / Claude. Extracts `[N]` citation markers from the answer. | `openai`, `anthropic`, `httpx` |
| **API** | `api.py` | FastAPI app: `/ask`, `/feedback`, `/document/{piece_id}`, `/health`. Loads the index once at startup (`lifespan`), refuses to start on unverified GPU backend. | `fastapi`, `uvicorn` |
| **Check suite** | `check.py` (~650 lines, largest after indexer) | 11 numbered invariants over the live index (corpus mapping coverage, size caps, metadata integrity, encoding, manifest consistency, coverage, fragment floor, dedup ledger, referentiel coverage, annexe section plausibility). Each has a `checks/known_*.json` allowlist for documented, accepted exceptions. | — |
| **Referentiel** | `referentiel.py` | Maps corpus files → "official document pieces" for citation resolution and the `/document` download endpoint. xlsx export/import for expert review. | `openpyxl` |
| **Sessions** | `sessions.py` | Append-only JSONL logging of every API call + feedback submission, for offline review (`aria-rag sessions`). | — |
| **Backend check** | `backend_check.py` | Verifies Ollama models are 100% GPU-resident before trusting latency/quality measurements (a documented, twice-bitten failure mode — see CLAUDE.md). | Ollama `/api/ps` |
| **Eval harness** | `eval.py` | Runs the golden dataset through retrieval (+ optional synthesis), scores by section-metadata match (not raw text), prints a formatted comparison table. | — |

---

## 4. Dependencies & integrations

**LLM/embedding backends** (pick one per call, via `--backend`/`ARIA_LLM_BACKEND`):
- **Ollama** (local, default on this machine) — `gemma3:4b` for query expansion, `ministral-3:8b` for synthesis. Requires `OLLAMA_LLM_LIBRARY=vulkan` on this machine for GPU (see CLAUDE.md/memory). **No API key needed; this is the EU-sovereignty-compliant path.**
- **OpenAI** — `gpt-4.1-mini` by default. Needs `OPENAI_API_KEY` (currently **unset** in `.env`).
- **Anthropic/Claude** — `claude-opus-4-6` by default. Needs `ANTHROPIC_API_KEY` (currently **unset** in `.env`).

**Embedding model** (retrieval, always local, no API): `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`, CPU-bound on this machine (~27 min to re-embed the full ~27k-chunk corpus from scratch — see indexer.py's embedding cache, added 2026-09-19, for the mitigation).

**Vector/lexical index**: FAISS (`IndexFlatIP`, no ANN, no ID-mapping — pure brute-force, positional) + `rank_bm25`'s `BM25Okapi`, fused by RRF. Both fully rebuilt on every index write (no incremental API for BM25; FAISS now has an embedding-level cache but the index structure itself is still a full rebuild each time).

**Document extraction**: `pypdf` (default path, all families) + Docling (`docling==2.128.0`, pinned) for section-aware chunking, currently applied to REG1_MS1 + annexes/oap/rapport_presentation/padd/cch (partial coverage — see SESSION_STATE.md for current per-family status). Docling is a `pyproject.toml` optional extra (`pip install -e ".[docling]"`), not a core dependency — only `scratch/` scripts import it.

**Notion**: `scripts/export_golden_cases.py` pulls the golden eval dataset from a Notion database ("Golden Cases v2"), needs `NOTION_API_KEY`.

**No hosted vector DB, no cloud storage** — everything (index, corpus, sessions) is local files under `data/` and `Ressources/`, both gitignored.

---

## 5. Data flow

```
Ressources/*.pdf
    │  loader.iter_pdf_paths + corpus_mapping.classify_path
    ▼
indexer.extract_chunks_from_pdf  (family-dependent chunking strategy)
    │  → Chunk{content, source_path, doc_family, page, section, ...}
    ▼
indexer.build_index
    │  dedup (corpus-wide content hash) → embed (sentence-transformers,
    │  cache-aware since 2026-09-19) → FAISS IndexFlatIP + BM25Okapi
    ▼
data/index/{chunks.json, index.faiss, bm25.pkl, manifest.json}
    │
    │  ── aria-rag check validates the above against checks/known_*.json ──
    ▼
retriever.search / search_weighted   (called from cli.ask or api.ask)
    │  optional query_expansion.expand_query first (Ollama, infers article codes)
    │  scoped_retrieval_merge: per-family fetch, RRF fusion, lexical boost
    ▼
list[SearchHit]  (content, source, page, section, scores)
    │
    ├─→ cli.format_hits → printed to terminal (--no-llm stops here)
    │
    └─→ llm.answer_question → llm.build_prompt (numbered [N] context) →
        backend call (Ollama/OpenAI/Claude) → answer text with [N] markers
            │
            ▼
        api.ask: extract_cited_markers, resolve document_url via referentiel,
        strip markdown → AskResponse{answer, citations[], session_id}
            │
            ▼
        sessions.log_ask_call → data/sessions/session_*.jsonl (always, even on error)
```

Evaluation is a parallel path: `eval.run_eval` drives the same retriever/llm functions against `eval/golden_dataset.json`, scores by whether a hit's **section metadata** (not raw text) matches the expected article/resource, writes `eval/results/results_<ts>_<hash>.json`.

---

## 6. Current state / open issues

See `SESSION_STATE.md` (newest first). Current state, open bugs, and recent fixes live there, not here, to avoid this file going stale. This document covers stable structure only.

---

*Living document — update the relevant section directly as the codebase's STRUCTURE changes. Dated status (fixes, open bugs, eval numbers) goes in `SESSION_STATE.md` instead, never here.*
