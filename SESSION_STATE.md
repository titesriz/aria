# SESSION_STATE.md

**Contract:**
- **START** of every session: read `CLAUDE.md` + the top 2 entries below before any diagnostic work.
- **END** of every session: append a new dated entry **above** this line (newest on top). Each entry: what was done (commits + one-line summaries), measurements produced (with `eval/results/` paths), decisions taken, and OPEN THREADS — pending questions, unverified hypotheses, the exact next step. Keep each entry under 15 lines.

---

## 2026-07-12 — serve hardening: startup retry + version visibility

**Done** (1 commit): two fixes for failure modes hit repeatedly this week, both in `backend_check.py`/`api.py`. (1) **Startup retry**: split `check_model_gpu` into a raising primitive (`_load_and_verify_gpu_once`) and two callers — `check_model_gpu` (unchanged, single-attempt, used where Ollama being down is a fact to report) and new `check_model_gpu_with_retry` (5 attempts, 2/4/8/16s backoff, used by `verify_backend` at startup) — retries **only** on `httpx.ConnectError` (Ollama not listening yet, the cold-boot race), never on a model that's reachable but lands on CPU (that's a real, distinct failure, reported immediately, not retried into a 30s wait). Each retry logs visibly (`logger.warning` + `print`); exhausting all 5 attempts still fails clearly, not an infinite wait. (2) **Version visibility**: new `resolve_git_commit(repo_root)` (`git rev-parse --short HEAD`, "unknown" fallback, never raises) — resolved once at startup, printed (`[startup] git_commit=...`), stored on `app.state.git_commit`, exposed in `/health` as top-level `git_commit` alongside `backend_status`.

**Measured**: full suite **158 passed** (was 150) — 8 new tests: 4 retry-path (refused-then-up succeeds, exhausts-and-fails-clearly, genuine-CPU-fallback-not-retried, single-attempt-still-immediate), 4 `resolve_git_commit` (hash on success, unknown on nonzero exit, unknown when git missing, unknown on subprocess error), plus `git_commit` assertions added to the two existing `/health` payload tests. `eval/` untouched.

**Verified live**: killed a stale `aria-rag serve` (PID 17512, predating today's changes) holding :8000, restarted after this commit. Startup log showed **zero retry lines** (Ollama already up) and `[startup] git_commit=` matching this session's commit hash (a commit can't quote its own hash in its own diff — see chat log for the exact verbatim `/health` payload reported to Anna, which does show it).

**Also**: committed the CH-07 research session's `SESSION_STATE.md` entry (previously 0 commits, read-only) after re-verifying it's still accurate — nothing touched `data/index/` since it was written.

**Open threads**: none new. The retry only guards the connection-refused race; if Ollama comes up but a model is still mid-load (e.g. answers a health-check-style ping but the real generate call times out rather than refusing), that's a different failure shape not covered here — flag if it recurs.

---

## 2026-07-12 — CH-07 corpus research (surélévation bureaux, zone UG)

**Done** (0 commits — read-only, no code/dataset changes): grepped `data/index/chunks.json` directly (not through the RAG pipeline) for the règlement provisions governing "surélévation d'un immeuble de bureaux à Paris" in zone UG. Delivered a ranked article table (verbatim excerpts + page/source) and a vocabulary-gap table to Anna/Charline for the CH-07 golden-case draft. Not yet written into `eval/golden_dataset.json` — pending Charline's review per her instruction.

**Key finding (non-obvious, corrects a likely wrong assumption)**: `UG.3.3.3` ("Surélévations destinées à l'Habitation") is the only height-bonus/dérogation article for surélévations in UG — and it's **explicitly Habitation-only**. A bureaux surélévation gets **no height bonus**; it's capped by the ordinary `UG.3.2.4` gabarit-enveloppe and the `Plan général des hauteurs` (graphic doc, `DG_E_HAUTEUR.pdf`), same as new construction. `UG.3.3.1` (dispositions générales on dépassements) names `UG.3.3.3` explicitly as Habitation-only, confirming this isn't a chunking artifact.

**Second finding**: bioclimatic obligations for a surélévation are **not** `UG.5.2` (constructions existantes) — that section explicitly excludes extensions/surélévations, redirecting to `UG.5.1` (constructions neuves, extensions, surélévations), which has separate numeric thresholds for "bâtiments de bureau au sens de la RE 2020" (Bbio -5%, DH≤500°h, Cep,nr -20%, carbone ≤710 kgCO2/m²) distinct from logement collectif.

**Vocabulary gap flagged for the expansion/ontology work**: "CINASPIC" (0 hits in `reglement_ecrit`, only appears in `rapport_presentation` as legacy/explanatory reference) — the 2025 PLU bioclimatique's actual binding destination name is "Équipements d'intérêt collectif et services publics". An architect query using "CINASPIC" would need this mapped, or expansion won't find the destination articles.

**Open threads**:
- CH-07 draft itself not yet written — next step is Charline's review of the ranked table before anything enters `eval/golden_dataset.json`.
- Didn't check `UG.7.2.2`/`UG.7.2.3` (stationnement bureau) or `UG.3.1.1`/`UG.3.1.2` (implantation) in depth — flagged as lower-priority/secondary checks, not in the ranked 3-6.
- Whether the specific bureaux building in Charline's case is patrimonially protected (`UG.2.4.1`) is unknown without the address — noted as conditional, not resolved.

---

## 2026-07-12 — repo hygiene

**Done** (1 commit, no code/server impact): deleted two unreferenced scratch files (`test.txt`, `page_end_sample.txt` — verified via repo-wide grep before deleting, zero hits). `.gitignore`: added `.claude/` (local Claude Code config) and `referentiel_*.xlsx` (regenerable export, `referentiel.yaml` is the source of truth) — both affect only *future* untracked files, not what's already committed (see decision below). Removed the `eval/results/` ignore rule and tracked all 93 existing result JSONs (5.2MB) — this is the fix-by-fix measurement history CLAUDE.md's audit-before-fix convention depends on (`eval/results/` paths cited per fix), and it had never actually been version-controlled.

**Decision — left already-tracked files alone**: `.claude/settings.json` and `referentiel_20260712.xlsx` were committed last session on explicit instruction ("commit and push all"); adding their patterns to `.gitignore` now doesn't retroactively untrack them. Asked Anna whether to `git rm --cached` both — she said leave them tracked as-is. So: new files under `.claude/` or new `referentiel_*.xlsx` exports won't be tracked going forward, but these two specific files remain in version control.

**Verified**: `git status` clean after the commit (nothing untracked, nothing modified).

**Open threads**: none new. Existing threads (U+008C warn-only, referentiel coverage gaps, `#page=19` anchor check) unchanged, see entries below.

---

## 2026-07-12 — encoding invariant known-failure allowlist

**Done** (1 commit): added `checks/known_encoding_failures.json` (same mechanics as `known_manifest_desync.json`) documenting the 42 U+FFFD chunks in `RP_DIAGNOSTIC.pdf` (font-subset corruption of periods, extraction audit finding, `family=rapport_presentation`/slot=0, zero production impact). `check_encoding` (`check.py`) now takes `settings` + an optional `known_encoding_path`, splits hard-fail hits into documented vs. new per `(source_path, char)` pair, and only ever suppresses **U+FFFD** — `_ALLOWLISTABLE_HARD_FAIL_CHARS = {"U+FFFD"}` hardcodes this so U+0000/U+0002 can never be silenced by this file even by mistake (tested: `test_encoding_fails_for_nul_byte_even_if_allowlisted`). Documented hits print a visible `known-failure (documented, see known_encoding_failures.json)` line rather than disappearing silently. 3 new tests (documented-pass, other-file-fails, nul-byte-cannot-be-allowlisted).

**Measured**: full suite 150 passed (was 147). `aria-rag check --strict`: **0 FAIL** (was 1) / 3 WARN / 6 PASS, exit 0.

**Decision — table doesn't match the original 7-PASS/1-WARN expectation**: the `4. Encoding` invariant itself is still **WARN**, not PASS — a separate, pre-existing, unrelated U+008C warn-only condition (176 chunks, "known leftover" per `check.py`'s existing comment, not touched this session) sits on the same invariant line and keeps it at WARN regardless of the FFFD fix. The actual 3 WARNs: `7. Fragment floor` (18 chunks, pre-existing), `4. Encoding` (U+008C, pre-existing), `9. Referentiel coverage` (2 gaps, pre-existing, per the prior session entry below). Net effect of this fix: FAIL→0, which was the actual goal (a permanently red check being ignored).

**Open threads**:
- U+008C warn-only (176 chunks) is still unaddressed by `loader.py` — separate, pre-existing debt, own future fix.
- Everything else from the prior 2026-07-12 entry (below) is unchanged and still open.

---

## 2026-07-12 — corpus referentiel + document-serving endpoint

**Done** (1 commit, uncommitted at session end pending review — see below): added `referentiel.yaml` (git-tracked, repo root) — a piece-level metadata manifest (`pieces:` hand-authored, `family`/`status_opposabilite`/`expected`/`file_match`; `files:` generated) layered on top of `corpus_mapping.yaml`'s file-level classification. New module `aria_rag/referentiel.py`. Seeded 17 pieces (15 present + 2 expected-but-absent: `annexe-sanitaire`, `annexe-liste-servitudes` — both `notes: "à confirmer"`) covering all 413 indexed PDFs with zero unmapped files. New CLI: `aria-rag referentiel export`/`import` (xlsx round-trip via openpyxl, French headers, status dropdown) — round-trip test (export→import→regenerate) passes byte-identical. New check invariant "9. Referentiel coverage" (FAIL on unmapped indexed file, WARN on expected-but-absent piece) — wired into `run_checks`. New `GET /document/{piece_id}` endpoint (inline PDF, path-safety via referentiel-only lookup, 404 for multi-file pieces e.g. atlases) + `document_url` on `/ask` citations (only for single-current-file pieces). Added `openpyxl` dependency.

**Measured**: full test suite 147 passed (12 new referentiel tests, 4 new `/document` tests, 4 new check tests). `aria-rag eval --no-llm` retrieval = 80% (matches documented baseline exactly, confirming no retrieval regression). `aria-rag check`: 9 invariants, 1 pre-existing unrelated FAIL (see below), 2 WARN (referentiel coverage gaps — both documented/expected), rest PASS.

**Verified**: restarted `aria-rag serve` (a stale pre-session process on :8000 was serving old code — killed it, confirmed via `/health`'s `last_check` timestamp). `/document/reg-ecrit-t1` confirmed end-to-end through the live ngrok tunnel via `curl`: HTTP 200, `Content-Disposition: inline`, downloaded bytes verified as a genuine 250-page PDF. Did **not** visually verify the `#page=19` browser anchor — no browser-automation tool is available in this environment; that fragment is interpreted client-side by the browser's native PDF viewer, so it needs a human check.

**Found, not fixed** (pre-existing, unrelated, out of scope — flagged per audit-before-fix): encoding invariant FAILs on 42 chunks (U+FFFD) all in `RP_DIAGNOSTIC`, predating this session (chunks.json unchanged since 2026-07-10). Also: `src/aria_rag/sessions.py` and part of `cli.py` (the `aria-rag sessions` command, `read_all_entries`/`format_entry_digest`) were already modified-but-uncommitted at session start — functionality already documented in `CLAUDE.md` §4 but never committed. Bundled into this session's single commit since it was already interleaved in `cli.py`'s diff; flagged to Anna for awareness rather than split out.

**Open threads**:
- `#page=19` anchor: needs a human to open `<ngrok>/document/reg-ecrit-t1#page=19` in an actual browser tab and confirm.
- Two coverage gaps (`annexe-sanitaire`, `annexe-liste-servitudes`) need Charline's confirmation — are they genuinely absent from the corpus, or misfiled under an existing piece?
- `annexe-plans-sup` and `annexe-plans-autres-perimetres` notes marked "à confirmer" — granularity/naming needs a domain-expert pass before this goes further.
- Encoding FAIL (42 chunks, `RP_DIAGNOSTIC`, U+FFFD) is untouched — next session should audit it separately (own fix, own before/after measurement, per CLAUDE.md §3).
- Next step: `referentiel_YYYYMMDD.xlsx` export → Charline review → `aria-rag referentiel import` before Charline's PLU retest (Notion is still the roadmap source of truth).

---

## 2026-07-11 — post-grounding-fix, post-markers, pre-retest

**Done** (commits `da52e38`..`5e65177`): backend CPU-fallback guard (`da52e38`); split expansion/synthesis models, wired ministral-3:8b for synthesis (`8c5e22f`); fixed silent prompt truncation via explicit `num_ctx` (`878b5ea`); strengthened grounding contract in the synthesis system prompt (`b41d076`); added `[N]` citation markers + anti-fabrication clause + `/feedback` endpoint (`5e65177`).

**Measured**: 3 fidelity-grid runs, sequential, same 13-case grid (golden + adversarial) — `eval/results/synthesis_fidelity_20260711_{baseline,fixed,markers}.json` (08:47, 09:18, 12:49). Answer-coverage rose baseline→fixed (~62%→70% on the 10-case golden subset); adversarial 3-case set scored 100/100 retrieval+answer across all three labels.

**Open threads**:
- Serve hardening not yet done: no retry/backoff and no git hash in `/health`'s `backend_status` payload (`api.py:223`) — needed to make stale-process detection (see CLAUDE.md §3) actually automatic instead of manual.
- Marker reliability still ~30% failure (missing/out-of-range `[N]`) — UI hides chips on failure but root cause in `llm.py` unaddressed.
- CCH dual-source prototype: Stage A done (`corpus_mapping.yaml`, config-driven classification) — Stage B/C (real scoped-retrieval slots for `cch`, currently in `KNOWN_UNSERVED_FAMILIES`) not started.
- CH-07 (next Charline case) — draft pending, not yet in `golden_dataset.json`.
- Next step: Charline retest itself — task source of truth is Notion, not this file.
