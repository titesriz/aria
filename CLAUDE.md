# CLAUDE.md

Operational guide for Claude Code working on ARIA.

**ARIA:** RAG pipeline for architects — answers regulatory questions about Paris zoning (PLU bioclimatique) and national building code (CCH), grounded in retrieved documents. Owner: Anna. Convention: code and technical writing in English, business/user-facing docs in French — this file, docstrings, commit messages are English; `eval/README.md`, prompts, and answers are French.

---

## BEFORE EVERY SESSION (start here)

Do these three things before you touch code:

### 1. Check the current state (2 min)

```bash
git status                           # working tree clean?
aria-rag check                       # corpus valid?
curl http://localhost:8000/health    # stale server running?
```

### 2. What are you actually doing?

- Fixing a bug? → Read top 2 entries of `SESSION_STATE.md` (what broke, what was tried)
- Adding a feature? → Read `PROJECT_MAP.md` §3 (what components you'll touch)
- Changing retrieval/eval? → Read "§3: Eval Harness" below, then measure before/after
- Restarting the server? → Kill old processes, check `/health` (see "I'm restarting the server" below)

### 3. The routing rule

If you need architecture context, read `PROJECT_MAP.md` (not this file). This file is about what not to break. `PROJECT_MAP.md` is about how things work. `SESSION_STATE.md` is about what's currently true (open bugs, recent fixes, latest eval numbers) — never hardcode dated facts here that belong there.

---

## THE 6 NON-NEGOTIABLE RULES

Read these. Memorize them. Don't break them.

### Rule 1: Audit Before Fix

Do this:
1. Confirm the bug exists (what exactly is wrong?)
2. Understand the root cause (where does it come from?)
3. Fix ONE thing only
4. Run `aria-rag eval` → measure the before/after
5. Commit (with eval results in the message)

Don't do this: fix three things, run eval once, hope it worked. You won't know which fix helped.

### Rule 2: Eval Baseline Is Only a Sentinel

The 80.0% / 91.7% numbers are **both retrieval scores** — same 10-case legacy dataset, without vs. with query expansion. They are NOT "the pipeline's accuracy," and 91.7% is not an answer-quality number.

They measure one thing only: did this change move the score compared to the same (flawed) yardstick? The legacy dataset itself contains **invented content that was never in the source material** — a fabricated `DG_E_HAUTEUR.pdf` document expectation for UC-01, a fabricated "H/2 min 6m" prospect rule for UC-03, a fabricated "notamment le 8e" qualifier for UC-04 (see `eval/golden_dataset_LEGACY.json`, frozen, read-only). No correct answer could satisfy a fabricated expectation, so the baseline itself is a lower bound on how wrong it could tell you that you are, not a ceiling on quality.

Bottom line: don't quote 80.0%/91.7% as absolute. Use them only as a relative "did we regress?" check, run **sequentially, never concurrently** — concurrent eval runs corrupt the shared query-expansion cache (`eval/expansion_cache.json`).

### Rule 3: Metadata Wins Over Text

When scoring retrieval, only the chunk's `section` field counts — not whether the text mentions an article code.

Example: Annexe X (a building list) mentions "UG.2.2.3" in passing. Don't score it as a hit for "UG.2.2.3" — it's about the building, not the rule. (This is the "magnet-chunk" lesson, `f17a9fc` — a chunk can mention a code without being about it.)

### Rule 4: Check `/health` Before Trusting Anything Live

If you restart the code and answers don't change, you probably have a stale process still running.

```bash
curl http://localhost:8000/health | jq '.git_commit'
# Should show your current commit. If it's old, you have a stale process.
```

This has bitten the project 3 times: identical byte-for-byte answers served hours apart from a process that never picked up the fix. Also note: two answers that ARE byte-identical hours apart is *expected*, not evidence of staleness on its own — `temperature=0` everywhere means identical inputs always produce identical outputs. Use `/health` and `synthesis_model_was_resident`, never answer-text diffing, to actually diagnose staleness.

### Rule 5: Grounding Is Mandatory

Answer ONLY from what the retriever found. Never invent.

If the context doesn't explain something, say so ("le contexte ne précise pas...") — don't fill the gap with your own knowledge (see `eval/fidelity_method.md`'s SUPPORTED / UNSUPPORTED / GENERIC methodology before touching `llm.py`'s `SYSTEM_PROMPT`). Mark each fact with `[N]` (the chunk index). These markers are ~30% unreliable (missing or out-of-range), but `extract_cited_markers()` falls back gracefully and the UI hides citation chips when markers are absent rather than showing broken ones.

### Rule 6: Production Is Ollama-Only

Sovereignty rule: `aria-rag ask`/`serve`/`eval`'s normal path, and the `/ask` API, must never call a US cloud LLM API — enforced at three independent layers (`llm.answer_question`'s dispatch, `cli.py`'s `--backend` choices, `api.py`'s synthesis-model lookup; see `tests/test_backend_guard.py`). OpenAI is removed entirely. `llm.answer_with_claude` still exists but is reserved for the eval-only no-corpus baseline (`aria-rag eval --baseline-no-corpus`) — if you ever find yourself wiring it into a production path, that's the bug, not a feature.

---

## TASK ROUTING

**I'm fixing a bug**
1. Open `SESSION_STATE.md`, read the top 2 entries → understand what's already known
2. Audit the bug: reproduce it, trace the root cause
3. Look at `PROJECT_MAP.md` §3 to find which component owns it
4. Fix one thing, measure, commit

**I'm adding a feature**
1. Read `PROJECT_MAP.md` §3 (Components) → understand what you'll touch
2. Think through the blast radius: if I change X, what else might break?
3. If it touches retrieval/eval, read "§3: Eval Harness" below
4. Implement, measure if it affects eval, commit

**I'm restarting the server**

```bash
# macOS/Linux
pkill -f aria-rag

# Windows (git-bash) -- pkill isn't available here
tasklist | grep aria-rag.exe   # find the PID(s)
taskkill //F //PID <pid>       # kill each one

aria-rag serve                                          # start fresh
curl http://localhost:8000/health | jq '.git_commit'     # verify it's the new code
```

**I'm running an eval**

```bash
aria-rag eval --no-llm         # retrieval only
aria-rag eval                  # + LLM-judged answer synthesis
aria-rag eval --baseline-no-corpus [--baseline-answers-file PATH]   # PoC: ARIA vs no-corpus baseline
```

Read "§3: Eval Harness" below before interpreting the numbers.

---

## FULL REFERENCE (read as needed)

### §1: Architecture Summary

**Offline phase (ingest, `indexer.py`)**:
- `loader.py` → extract PDFs from `Ressources/`
- `corpus_mapping.yaml` → classify files by path (which family, norm_level, city, validity) — longest-matching prefix
- Chunk (1200/200 overlap, article-header-aware for `reglement_ecrit`) → embed (`paraphrase-multilingual-mpnet-base-v2`) → FAISS `IndexFlatIP` (dense) + BM25Okapi (keyword)
- Extraction and embedding are cached for unchanged files; the FAISS/BM25 index structure itself is still rebuilt in full on every write (no incremental index API)
- `aria-rag check` runs automatically after, enforcing corpus invariants (`check.py`)

**Online phase (ask, `retriever.py` + `llm.py`)**:
- Query expansion (`gemma3:4b`) → infer likely PLU article codes
- Hybrid retrieval (FAISS + BM25 fused by Reciprocal Rank Fusion, RRF_K=60) scoped per-family (`config.py`'s `DEFAULT_FAMILY_SLOTS`) → top-k hits
- Synthesis (`ministral-3:8b`, **Ollama only** — see Rule 6) → grounded answer with `[N]` citations, via `llm.py`'s grounding-constrained `SYSTEM_PROMPT`

Details: see `PROJECT_MAP.md` §2-5 for the full module-by-module breakdown and data flow.

### §2: GPU & Model Residency

- Two models: `gemma3:4b` (expansion) and `ministral-3:8b` (synthesis) — both don't fit in 8GB VRAM, so Ollama swaps between them per call. Expect ~20s of extra latency when `synthesis_model_was_resident=False` in session logs — expected, not a bug.
- Both run at `temperature=0` (deterministic, byte-identical outputs across runs — see Rule 4).
- **GPU requires `OLLAMA_LLM_LIBRARY=vulkan`** on this machine (CUDA PTX crashes otherwise). `backend_check.py` force-loads each model and checks `/api/ps`'s `size_vram == size` before trusting any latency measurement — silent CPU fallback (model reports "loaded" but actually runs on CPU) has bitten this project twice.
- `aria-rag serve` refuses to start unless the GPU check passes; `--allow-cpu` bypasses that guard for deliberate CPU-mode testing only (expect much higher latency, don't trust measurements from it as representative).

### §3: Eval Harness

Datasets:
- `eval/golden_dataset.json` — 12 cases (UC-01/02/03/04/05/16, CH-01/02/03/04/05/06), generated **only** by `scripts/export_golden_cases.py` from the Notion "Golden Cases v2" database — never hand-edited. `validated==true` cases are certified; everything else is pending and reported in a **separate** table, never blended into a headline number. Check `SESSION_STATE.md` for the current validated count — don't trust a number hardcoded here, it will go stale.
- `eval/golden_dataset_LEGACY.json` — frozen, read-only, for non-regression comparison only (see Rule 2 for why it's not a ground truth).
- `eval/adversarial_dataset.json` — 3 cases (ADV-A/B/C) testing weak-retrieval honesty, off-corpus refusal, fabricated-reference honesty.
- `eval/fidelity_method.md` — claim-level grounding methodology; read before touching `SYSTEM_PROMPT` (Rule 5).

Commands:
```bash
aria-rag eval --no-llm          # retrieval only
aria-rag eval                   # + LLM-judged synthesis (judge.py)
aria-rag eval --baseline-no-corpus [--baseline-answers-file PATH]  # PoC: ARIA vs no-corpus baseline
aria-rag check                  # corpus invariants (see §4)
aria-rag sessions --last N      # session logs
```

Why scores move:
- **Retrieval**: you changed how documents are indexed or ranked.
- **Synthesis/judge**: you changed the system prompt, the model, or `judge.py`'s scoring formula (document any formula change).
- **Baseline corruption**: you changed `corpus_mapping.yaml` and didn't re-index — verify `aria-rag check`'s corpus-mapping-coverage invariant first.
- **Eval harness itself**: you changed the scoring function — this must be its own commit, measured separately from any pipeline change (Rule 1).

Run comparisons **sequentially, never concurrently** (Rule 2 — shared expansion cache).

### §4: Corpus Invariants

Run `aria-rag check --strict` before committing. It runs 11 numbered checks: corpus mapping coverage, size caps, metadata integrity, encoding, manifest consistency, coverage, fragment floor, dedup ledger, referentiel coverage, annexe section plausibility, and one more (see `check.py`).

Known failures are allowlisted (`checks/known_*.json`) — matched by the exact documented specifics (path *and* the recorded numbers, where applicable), not just enough to make the check pass. So old debt stays green while a genuinely new instance of the same problem still fails loudly. **Never widen an allowlist to silence a new failure** — that defeats the point.

### §5: What Not to Change Without Measuring

- `llm.py`'s `SYSTEM_PROMPT` → read `eval/fidelity_method.md` first, then run `aria-rag eval` before/after.
- `retriever.py`'s RRF weighting or lexical boost → run `aria-rag eval` before/after.
- `indexer.py`'s chunking strategy → re-ingest, run `aria-rag eval` before/after.
- `config.py`'s `DEFAULT_FAMILY_SLOTS` → run `aria-rag eval` before/after.
- `corpus_mapping.yaml` → re-ingest, run `aria-rag check`, run `aria-rag eval`.
- `judge.py`'s `compute_final_score` formula → re-run `aria-rag eval --baseline-no-corpus`, document the formula change in the commit.

If you touch any of these, measure. No exceptions (Rule 1).

### §6: Session Protocol

Every session, before you commit: write a dated entry at the **top** of `SESSION_STATE.md` (newest first):

```
## YYYY-MM-DD — [one-line summary]

**Done:** [commits, one-line each] / [measurements with file paths] / [decisions]

**Open threads:** [what's not finished, what needs a human call, exact next step]
```

This is how future-you (and other developers) avoid re-diagnosing the same bug.

Example:
```
## 2026-09-22 — fixed CH-06 row ranking

**Done:** Added `table_type` chunk field (`3c32025`). Re-ingested `reglement_ecrit`.
Ran `aria-rag eval` → 80.0% (unchanged).

**Open threads:**
- CH-06 still ranks 0/13 "1er" addresses; needs retrieval ranking audit (separate from chunking fix).
- Did this change affect synthesis latency? Not measured yet.
```

`SESSION_STATE.md` owns dated/current-state content; `PROJECT_MAP.md` owns stable structure. Don't duplicate one into the other.

---

## DECISION CHECKLIST: BEFORE YOU COMMIT

- [ ] Did I audit the bug/feature first?
- [ ] Did I change only ONE thing?
- [ ] If it touches retrieval/eval, did I measure before/after?
- [ ] Does `git status` show only what I intended to change?
- [ ] Did I run `aria-rag check --strict` (0 new FAIL)?
- [ ] Did I write a `SESSION_STATE.md` entry describing what I did?

---

## QUESTIONS THIS FILE DOESN'T ANSWER

- "How does component X work?" → Read `PROJECT_MAP.md` §3
- "What should I work on next?" → Read `SESSION_STATE.md` top 2 entries, or check Notion (the task/roadmap source of truth)
- "How do I set up the repo?" → Read `README.md`
- "Why was choice Y made?" → Read `SESSION_STATE.md` entries from when that choice was made

---

## Documentation & Notion sync (ARIA / ARGI.IA)

Notion is the source of truth for the POC documentation. The repo is the source of truth for code only. The Notion MCP connector must be connected at session start. If it is not, say so before doing anything that depends on documentation.

### Pages (read in this order at the start of every session)

1. **Architecture produit**: reference for system state (actual vs target). The section "État réel vérifié — 06/10" is at the top. https://www.notion.so/Architecture-produit-3dd3ae1f2a6d815b8d62c4d4550ca464 (id `3dd3ae1f2a6d815b8d62c4d4550ca464`)
2. **Roadmap exécution (Chunking / Routage post-cadrage)**: execution plan with phases and checkboxes, deadline 2026-10-16 (POC validation, PLU only). https://www.notion.so/Roadmap-ex-cution-Chunking-Routage-post-cadrage-3ed3ae1f2a6d81eaae3ac7161ea0c39d (id `3ed3ae1f2a6d81eaae3ac7161ea0c39d`)
3. **ARIA V2 — pipeline refacto** (dev journal / dev log): dated history. NOT the truth: if it conflicts with Architecture produit, Architecture produit wins. https://www.notion.so/ARIA-V2-pipeline-refacto-3e43ae1f2a6d8033bbb3d5fbc3ca7170 (id `3e43ae1f2a6d8033bbb3d5fbc3ca7170`)

### Rules

- **Timestamp first.** Before inserting any dated entry in Notion, get the current date/time from the system clock (e.g. `date '+%Y-%m-%d %H:%M %Z'`). Never infer or reuse a date from memory or earlier context.
- **Dev log V2** = page "ARIA V2 — pipeline refacto", an entry of the Notion database "Dev Journal"; daily suites go in that page. Entries are appended as `## YYYY-MM-DD (suite N)`, where N = previous suite + 1. Fetch the page and check the last suite number first. Content: what was done, decisions, numbers, open items. Keep it factual.
- **End of every task, report state changes:**
  - Done / diverging from Architecture produit → propose the exact edit (page, section, new text).
  - Roadmap checkboxes that changed.
  - New dev log entry.
  - Apply them in Notion if the connector is available; otherwise output them as a ready-to-paste block.
- **Conflict repo vs Notion** (code says X, Notion says Y): flag it explicitly, do not silently pick one. Verified reality (code, DB counts, run output) goes into Architecture produit with the verification date.
- **Mark verification status** in what you write: *verified* (command/query run today), *assumed*, or *unknown*. Never write "done" for something not run.
- **Do not rewrite or delete Notion content wholesale.** Use targeted edits. Archived pages ("framework for raw pdf processing") are history only.
- **Scope for the 2026-10-16 POC: PLU only.** CCH and Code de l'urbanisme JSONL (local) are post-validation; do not process them now.

### Working conventions

- One session per topic / set of files. Avoid concurrent writers on `ontology.db` (SQLite locks: use `busy_timeout`; open read-only for review tooling; verdicts go in a separate DB).
- Code and technical docs in English; business/strategy in French.
