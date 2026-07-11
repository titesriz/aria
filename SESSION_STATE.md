# SESSION_STATE.md

**Contract:**
- **START** of every session: read `CLAUDE.md` + the top 2 entries below before any diagnostic work.
- **END** of every session: append a new dated entry **above** this line (newest on top). Each entry: what was done (commits + one-line summaries), measurements produced (with `eval/results/` paths), decisions taken, and OPEN THREADS — pending questions, unverified hypotheses, the exact next step. Keep each entry under 15 lines.

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
