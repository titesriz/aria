# SESSION_STATE.md

**Contract:**
- **START** of every session: read `CLAUDE.md` + the top 2 entries below before any diagnostic work.
- **END** of every session: append a new dated entry **above** this line (newest on top). Each entry: what was done (commits + one-line summaries), measurements produced (with `eval/results/` paths), decisions taken, and OPEN THREADS — pending questions, unverified hypotheses, the exact next step. Keep each entry under 15 lines.

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
