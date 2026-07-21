# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 1. What this is

ARIA — a RAG pipeline for architects, answering regulatory questions grounded in the **PLU bioclimatique de Paris** (zoning: heights, setbacks, mixed-use) and the **CCH** (Code de la Construction et de l'Habitation, national). Owner: Anna. Convention: code and technical writing in English, business/user-facing docs in French — this file, docstrings, commit messages are English; `eval/README.md`, prompts, and answers are French.

## 2. Architecture in 10 lines

**Offline (ingest, `indexer.py`)**: `loader.py` walks `Ressources/`, extracts PDF text with `pypdf` → `corpus_mapping.yaml` (config-driven, via `aria_rag.corpus_mapping`) classifies every file into `{family, norm_level, city, validity}` by longest-matching prefix → text is chunked (1200/200 overlap), filtered by `min_alpha_ratio` → embedded with `paraphrase-multilingual-mpnet-base-v2` into a FAISS `IndexFlatIP` + a pickled BM25Okapi index, alongside a manifest for incremental re-ingestion → `aria-rag check` runs automatically after, enforcing corpus invariants (`check.py`).

**Online (ask, `retriever.py` + `llm.py`)**: query → expansion with **gemma3:4b** (`query_expansion.py`, infers PLU article codes, temperature=0, cached) → hybrid retrieval, FAISS + BM25 fused by **Reciprocal Rank Fusion** (RRF_K=60), scoped per-family with slots from `config.py`'s `DEFAULT_FAMILY_SLOTS` → top-k hits synthesized into an answer by **ministral-3:8b** via `llm.py`'s grounding-constrained system prompt.

## 3. Critical invariants & conventions (the expensive lessons)

- **Audit before fix; one fix at a time; measure between each.** Every fix in git log (grounding, markers, num_ctx, num_predict) shipped with a before/after eval run in `eval/results/` — never bundle a fix with the measurement of a different fix.
- **Eval reference: 80.0% baseline retrieval / 91.7% with query expansion**, computed across the (now legacy, see §4) 10-case golden dataset. Comparisons must be run **sequentially, never concurrently** — concurrent runs corrupt the shared query-expansion cache (`eval/expansion_cache.json`). **Read as a relative before/after delta only** (did this specific change move the number), never as an absolute/certified score — see §4's "Golden dataset provenance."
- **The scorer matches section METADATA, never raw chunk text** (the "magnet-chunk" lesson, `f17a9fc`) — a chunk can mention an article number in passing without being about it; only the chunk's own `section` field counts as a hit.
- **gemma3:4b (expansion) and ministral-3:8b (synthesis) do not co-reside in 8GB VRAM.** Ollama swaps between them per call — expect ~20s of extra latency when `synthesis_model_was_resident=False` in session logs. This is known and expected, not a bug.
- **GPU via `OLLAMA_LLM_LIBRARY=vulkan`.** `backend_check.py` force-loads each model and checks `/api/ps`'s `size_vram == size` before trusting any latency measurement — silent CPU fallback (stale Vulkan-env-var config, model reports "loaded" but runs on CPU) has bitten this project twice already. `aria-rag serve` refuses to start on an unverified GPU unless `--allow-cpu` is passed.
- **Beware stale `aria-rag serve` processes left running on port 8000** after a code change — bitten 3× (identical byte-for-byte answers served hours apart from a process that never picked up the fix). Before trusting any live-server measurement, hit `GET /health` and check `backend_status` reflects the change you expect, not an old resident process.
- **Synthesis grounding contract** (`llm.py`'s `SYSTEM_PROMPT`): answer only from provided context, state gaps explicitly ("le contexte ne précise pas..." — see `eval/fidelity_method.md`'s GENERIC category) rather than filling them, and mark each factual claim with a `[N]` chunk-index marker. Markers are **unreliable ~30% of the time** (missing or out-of-range) — `extract_cited_markers()` falls back gracefully, and the UI hides citation chips when markers are absent rather than showing broken ones.
- **temperature=0 everywhere** (expansion and synthesis). Identical inputs produce identical outputs — two answers that are byte-identical across hours apart are the deterministic pipeline working correctly, not a sign of a stuck/stale process. (Use `/health` and `synthesis_model_was_resident`, not answer-text diffing, to actually diagnose staleness.)

## 4. Eval harness

- `eval/golden_dataset.json` — 12 cases (UC-01/02/03/04/05/16, CH-01/02/03/04/05/06), generated **only** by `scripts/export_golden_cases.py` from the Notion "Golden Cases v2 — Dataset unifié" database — never hand-edited. Each case carries a `validated` bool (Notion's "Validé Charline" checkbox); `aria-rag eval` reports certified (`validated==true`) and pending results as two **separate** tables/averages — a pending case's score is never blended into a headline number. As of 2026-07-21, 0/12 cases are `validated==true` (all pending expert review) — don't present any run's score as certified until Notion says otherwise for the relevant case(s).
- `eval/golden_dataset_LEGACY.json` — the pre-2026-07-21 hand-simplified dataset, frozen, read-only, kept **only** as a diff-only / relative-non-regression reference (see "Golden dataset provenance" below). Never re-generate or hand-edit it either.
- `eval/golden_export_diff.md` — auto-generated old-vs-new diff (added/dropped cases, changed questions/expected articles) from the last `export_golden_cases.py` run.
- `eval/adversarial_dataset.json` — 3 cases (ADV-A/B/C): weak-retrieval honesty, off-corpus refusal, plausible-but-fabricated-reference honesty.
- `eval/fidelity_method.md` — claim-level grounding methodology (SUPPORTED / UNSUPPORTED / GENERIC) for scoring synthesis changes independently of retrieval quality; see it before touching `SYSTEM_PROMPT`.
- Commands: `aria-rag eval` (golden dataset, retrieval + answer-coverage scores), `aria-rag check` (corpus invariants), `aria-rag sessions --last N` (read-only digest of logged `/ask` calls — question, hits, answer, synthesis model, latencies).
- Known-failure allowlists (`checks/known_*.json`) exist so **today's** documented debt stays green while a genuinely new instance of the same problem fails loudly — never widen an allowlist to silence a new failure; that defeats the point.

**Golden dataset provenance (read before citing any eval score as ground truth).** The pre-2026-07-21 `golden_dataset.json` (now `golden_dataset_LEGACY.json`) was a lossy, hand-simplified export of Charline's real evaluation cases — it **invented content** that was never in the source material (a fabricated `DG_E_HAUTEUR.pdf` document expectation for UC-01, a fabricated "H/2 min 6m" prospect rule for UC-03, a fabricated "notamment le 8e" qualifier for UC-04). The §3 reference scores (80.0% / 91.7%) were measured against that file. They were only ever useful as a **relative non-regression sentinel** — did a given code change move the number, run against the same flawed yardstick both times — never as an **absolute measure of pipeline quality**, because the yardstick itself contained fabricated expectations no correct answer could have satisfied. Don't optimize toward 80.0%/91.7% as if they were a certified ceiling or floor; don't quote them in isolation as "the pipeline's accuracy." The Notion-sourced `golden_dataset.json` replaces it as the instrument going forward, but per the paragraph above, its own scores aren't certified either until the relevant cases are marked `validated==true` in Notion.

## 5. Current state & roadmap

Phase: **post-grounding-fix, post-citation-markers, pre-retest** (Charline's real-world retest is the next milestone). **Notion is the task/roadmap source of truth** — this file is a map to the code, not a substitute for the project board; check Notion for what's next and who owns it.

## 6. Session protocol

See `SESSION_STATE.md` (repo root) for the full contract. In short: **read `CLAUDE.md` + the top 2 entries of `SESSION_STATE.md` before any diagnostic work**, and **append a dated entry at the end of every session** (newest on top) — what was done, what was measured, what was decided, and the open threads the next session needs.
