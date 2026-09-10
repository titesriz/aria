# SESSION_STATE.md

**Contract:**
- **START** of every session: read `CLAUDE.md` + the top 2 entries below before any diagnostic work.
- **END** of every session: append a new dated entry **above** this line (newest on top). Each entry: what was done (commits + one-line summaries), measurements produced (with `eval/results/` paths), decisions taken, and OPEN THREADS — pending questions, unverified hypotheses, the exact next step. Keep each entry under 15 lines.

---

## 2026-09-09c — scratch/ fix: Rapport_presentation_MS1 2-column anchor match 90%→100% (0 commits, read-only, scratch/ only)

**Done**: fixed the residual column-boundary bug in `scratch/extract_sommaire_tree_rp.py` (edited in place, not rewritten — narrow fix per instruction) without touching the column-detection decision itself (block-level split, unchanged). Root cause was NOT column mis-assignment as first assumed — PyMuPDF splits a single visual text row into multiple word-level "line" objects (confirmed: `"Correction d'une erreur de saisie concernant les"` came back as 7 separate fragments at slightly different x0, some crossing the split point purely because a wide left-column sentence extends past it). Two-part fix: (1) bucket by the BLOCK's own x0 (clean, reliable) instead of each line-fragment's own x0, so a whole sentence travels with its column regardless of individual word x-positions; (2) coalesce fragments sharing a block AND a y-band (±1.5pt) back into one logical line before grouping — needed because a leftover mid-sentence house number (e.g. "79" in "...couleur au 79 Jean-Pierre Timbaud...") was still falsely triggering "entry closed" once column-bucketing alone was fixed.

**Result**: Rapport_presentation_MS1 anchor match 90%→**100%** (10/10), warnings 15→7 (all now the expected structural categories, zero corrupted/garbled entries), entries 26→27. Non-regression: RP_CHOIX stayed 100% anchor throughout (110→103 entries — a genuine side-effect of the same coalescing fix removing previously-fragmented spurious pseudo-entries, not a loss; no corruption found on inspection). REG2A1_MS1 byte-identical to its prior validated result (12 entries, 100% anchor, same 2 warnings verbatim).

**Open threads**:
- The coalescing fix (part 2) is generic (block+y-band based) but was only exercised on this one document — worth keeping in mind if a future document shows the same word-fragmentation pattern, to confirm it still helps rather than over-merges.
- `scratch/extract_sommaire_tree_relative.py` (the relative-level rework script) has its OWN copy of the column logic, now stale relative to this fix — not updated here since this task scoped the fix to `extract_sommaire_tree_rp.py` specifically; flag if the relative-level tree for Rapport_presentation_MS1 is needed again with 100% anchor.

---

## 2026-09-09b — scratch/ study: transverse rework — RELATIVE level rule replaces absolute-depth (0 commits, read-only, scratch/ only)

**Done**: replaced "level = absolute depth of numbering code" with a line-by-line RELATIVE rule (`scratch/relative_level_engine.py`, driven by `scratch/extract_sommaire_tree_relative.py`) across all 6 documents studied so far. Rule: a brand-new numbering profile after a different one goes one level deeper; a NESTING profile (digit-dot chains, lettered zone-codes) moves by signed depth-delta vs. its own last occurrence; a FLAT profile (PARTIE n, Annexe, Axe n, single letter/roman, ALL-CAPS heading) that recurs ANYWHERE jumps back to its first-occurrence level regardless of what happened in between; unnumbered/non-caps lines inherit the previous level. Each family's line-extraction (REG1 header-based, REG2/REG2A10 title-search, RP column/margin-band) reused byte-for-byte, unchanged.

**Non-regression (checked first, per instruction)**: all 4 prior docs — **extraction itself byte-identical** (same entry count, same numero/titre/page_debut sequence/order) for REG1 (161), REG2A1 (12), REG2A10×2 (10 each). REG2A10's flat-level-1 invariant holds exactly. Only `niveau` redistributed, and every shift traces to a named rule: REG1's 4 zone-header "Caractère de la zone..." lines moved 3→2 (now inherit their ALL-CAPS header's level instead of nesting under it); REG2A1's 1 stray unnumbered paragraph moved 3→2 (same reason). **Flagged, not resolved**: task said "REG1_MS1 (58 kept sections)" — the saved tree has 161, both before and after; didn't force a match, reported the real number.

**RP_CHOIX Axe check (the task's explicit target) — passed**: `"1."`→level2, `"Axe 1/2/3"`→level3 **all three**, confirming the FLAT-recurring-jump rule works as specified (Axe2/3 snap back to Axe1's anchor regardless of the digit-dot chain's depth wandering in between). RP_CHOIX: 110 entries, `{1:1,2:4,3:10,4:47,5:25,6:23}`, 100% anchor. Rapport_presentation_MS1: 26 entries, `{1:4,2:21,3:1}`, 90% anchor (same pre-existing residual mismatch, unrelated to this change).

**Notable side-effect flagged for review**: because "La démarche de construction du PADD" (unnumbered) inherits level2 same as "1.", and "Axe 1" computes to level3, "Axe 1" ends up nested as a CHILD of "La démarche..." rather than a sibling under "1." — correct per the specified rule, but not how a human would read the document. Full write-up: `scratch/RELATIVE_LEVEL_FINDINGS.md`.

**Open threads**:
- REG1's "58 kept sections" figure needs clarification — doesn't match any tree produced in this study.
- The Axe-1-nests-under-La-démarche side effect should go to the domain expert before this feeds anything downstream — it's a real, disclosed consequence of "position not meaning," not a bug, but worth a second look.

---

## 2026-09-09 — scratch/ study: sommaire extractor on RP_CHOIX (control) + Rapport_presentation_MS1 (2-column hard case) — 4 real bugs found+fixed (0 commits, read-only, scratch/ only)

**Done**: ran the validated REG2A10 extractor **unchanged** on both targets first. Neither behaved as expected: RP_CHOIX (assumed 1-column "control") was badly broken (120 warnings) — its numbering tokens ("1.", "1.1.") sit alone on their own line, title on the next, a shape never seen before (old grouping only knows "continuation of preceding entry"). Rapport_presentation_MS1's sommaire wasn't detected at all — correctly triggered the loud "aucun sommaire détecté" warning, but root cause was a hardcoded `HEADER_BLOCK_LINES=5` (this doc's header is only 3 lines); its TOC pages are confirmed genuinely 2-column landscape.

**Fixed generically**, in a fork `scratch/extract_sommaire_tree_rp.py`: (1) merge isolated numbering-token lines with their title line — **mandatory trailing dot** required (without it, a bare page number like "26" was ambiguous with a real token "2." and got glued onto the next entry, producing a bogus level-1 node). (2) header length now *discovered* per document (marker search + next-line-content disambiguation) instead of a constant — reproduces REG1's original 5 exactly, finds 4 for RP_CHOIX, 2 for the hard case. (3) new `DIGIT_DOT` numbering type, same mandatory-dot fix needed again (a wrapped street address "14 rue René Villermé..." was misread as entry `numero="14"` without it). (4) column detection: first attempt clustered *lines* by x0 and broke twice — RP_CHOIX's decorative right-aligned header/footer created a false 2nd cluster (fixed: require ≥20% of content each side, not just "several lines"); the hard case's real 2-column page had noisy line-level x0 with no clean gap even though *block*-level x0 was clean (fixed: decide the split from blocks, apply it to lines). Also needed a 90pt absolute margin band excluding header/footer from column clustering — the footer must be **dropped outright, not repositioned** (repositioning caused a real bug: a footer line parsed as a bogus entry with page_debut=103, the doc's own total page count).

**Results**: RP_CHOIX 110 entries, 100% anchor match (was the "easy" one, ended up needing the most new rules). Rapport_presentation_MS1 (genuine hard case) 26 entries, 90% anchor match (9/10) — **not fully clean, disclosed not hidden**: a few entries still mix wrapped text across the column boundary on its densest page (full list in `scratch/REG2_RP_FINDINGS.md`), judged diminishing-returns to chase further this pass. Non-regression: REG2A1_MS1 re-run with this same hardened script — 12 entries, 100% anchor, byte-identical to its original validated result.

**Open threads**:
- Rapport_presentation_MS1's residual ~10% anchor miss and 2 garbled `numero=None` entries are real, unresolved — a future pass could try per-entry (not per-page) column assignment for lines that wrap across the boundary.
- The mandatory-trailing-dot fix (applied twice now, isolated-numbering and DIGIT_DOT) suggests a general principle worth promoting: any "bare numbering token" rule in this family should require the terminal punctuation that distinguishes it from incidental digits (page numbers, house numbers) — worth stating explicitly if a shared/generic extractor is ever built from these forks.

---

## 2026-09-08e — scratch/ study: sommaire extractor on REG2A10_1DE2/2DE2 (Annexe X, arr. 1-10/11-20) — 2 real generalizable bugs found+fixed (0 commits, read-only, scratch/ only)

**Done**: ran the validated REG2A1 extractor (`scratch/extract_sommaire_tree_reg2.py`) **unchanged** against `REG2A10_1DE2_MS1.pdf` (700p) first, per method. TOC detection and parsing worked (10 entries found), but the output was wrong in two ways — both are REG2A1 assumptions breaking on a new shape, not REG2A10-specific quirks: (1) **runaway nesting** (level 2→11 instead of 10 flat siblings) — REG2A1's "unnumbered entry nests under whatever's on the stack" rule assumed unnumbered entries are rare exceptions; here **all 10** entries are unnumbered (no "Annexe"/"partie" prefix at all — just ALL-CAPS "LISTE DES PROTECTIONS PATRIMONIALES DU Nème ARRONDISSEMENT" headings), so each nested under the previous one instead of being its sibling. (2) **false anchor-check offset** (-3, votes {0:1,-3:9}) — REG2A1's anchor matcher only checked the first 4 normalized words; all 10 titles here share those same first 4 words (identical to the running page header repeated on every page), so it kept matching neighbouring arrondissements' pages instead of the right one. Manually verified via `doc[47:52].get_text()` that the real per-document offset is **+1** (printed page N = physical index N), not -1 like REG1/REG2A1.

**Fixed generically** in a fork, `scratch/extract_sommaire_tree_reg2_a10.py` (REG2A1 script untouched): (1) unnumbered entries now nest under the last **numbered** ancestor (tracked separately), defaulting to level 1 — verified this doesn't change REG2A1's own result by hand-tracing it. (2) anchor check now matches the full normalized title, not a word-count prefix. Also wired in this task's robustness rules: loud "aucun sommaire détecté" path (unused here, real TOC found both times), last-branch-entry `page_fin` set to `doc.page_count` instead of null, every `numero=None` node flagged in warnings.

**Result (both files)**: 10 flat level-1 entries each, 100% anchor match, dominant offset +1 (9/10 votes). Outputs: `scratch/reg2a10_1de2_ms1_sommaire_tree.json`, `scratch/reg2a10_2de2_ms1_sommaire_tree.json`. Full write-up: `scratch/REG2A10_FINDINGS.md`.

**Open threads**:
- Flagged, not resolved: the task's "`numero=None` = fused into parent, not standalone" convention doesn't semantically fit here — these 10 unnumbered nodes ARE the document's real primary structure (one ~40-130 page arrondissement table each), not incidental text like REG2A1's stray paragraph. Needs a human call before Notion injection: keep the "fused" label as-is, or add a distinct category for "unnumbered but structurally primary."
- The per-document offset (+1 here vs -1 for REG1/REG2A1) is real and undocumented anywhere else — worth carrying as an explicit per-family field if/when this feeds a shared extractor, rather than assuming one offset convention repo-wide.

---

## 2026-09-08d — scratch/ study: page-1 title-layout survey across all 409 PDFs of "PLU bioclimatique" (0 commits, read-only, scratch/ only)

**Done**: `scratch/survey_page1_titles.py` — fast first-pass classifier (get_text + get_image_info only, no get_drawings, ~2min for 409 files) over the whole `Ressources/PLU bioclimatique/` tree. For each PDF: page-1 layout type + a title-extraction feasibility verdict based only on methods already validated this session (gap-Y for linéaire pages; a lightweight font-size-outlier proxy, NOT the full vector-frame detector, for graphic pages). Output: `scratch/page1_title_survey.csv` (409 rows).

**Bug caught before trusting results**: initial version misclassified `OAP_BARTHOLOME_BRANCION.pdf` — validated EARLIER THIS SESSION as a clean "couverture linéaire, OUI" case — as "planche graphique/INCERTAIN", purely because its full-bleed cover photo gives `image_area_ratio=1.0`, same as a real map plan. Fixed by checking span-count (≤20) BEFORE the image-ratio branch, since a short title over a background photo and a map with hundreds of labels are structurally different even at the same image coverage. Re-ran; `OAP_BARTHOLOME_BRANCION.pdf` now correctly lands back in "couverture / OUI".

**Final counts (post-fix)**: types — 216 "planche graphique avec encart", 107 "aucun texte exploitable (scan pur, e.g. all PPRI plans — spot-checked, genuinely 0 chars)", 49 "couverture/titre linéaire", 28 "sommaire dense", 9 "linéaire courant". Feasibility — 214 PROBABLE (graphic pages, font-outlier proxy only, **not** the heavier frame detector), 107 NON (no text at all), 65 OUI (linéaire/sommaire, methods already validated), 23 INCERTAIN (mostly short-span couverture pages with no dominant font size, 2 graphic pages with no size outlier at all — the ASUP2AD5 pattern).

**Open threads**:
- The "PROBABLE" bucket (214 files) is NOT frame-verified — it's the cheap proxy only. `scratch/detect_encart_rect.py` (the real `get_drawings()` check) has so far only run on 2 of these 216 files, with mixed results (1 legend frame found, 0 title-cartouche matches) — don't treat PROBABLE as validated at scale.
- `ASUP1_2025_12_19.pdf` (contains real "Annexe" text, flagged 2 entries ago) is still the best candidate to actually confirm the vector-frame hypothesis — not yet run.
- The 21 "couverture/INCERTAIN" files are worth a manual skim — could be a distinct sub-pattern (e.g. two same-size title lines, no dominant outlier) needing its own small rule.

---

## 2026-09-08c — scratch/ study: encart detector, 2nd file (A15152_01A04) — legend box found, title cartouche still not (0 commits, read-only, scratch/ only)

**Done**: added `A15152_01A04_2025_12_19.pdf` (`Ressources/.../Annexes/Plans autres périmètres/`) to `scratch/detect_encart_rect.py`'s `PDF_PATHS` and re-ran (still read-only, still no threshold coded). Only 57 spans on page 1; only 1 matches the anchor vocabulary at all, and it's a buried parenthetical ("(liste détaillée en annexe au PLU)"), not a title. **New positive signal though**: the script cleanly isolated a real vector-framed box — `bbox (0,842,595,1684)`, white fill + gray stroke, containing exactly 26 spans starting with "Légende" ("Le droit de préemption...", "et du 7ème arrondissement...", "Fond de plan..."). That's a genuine **legend** box, frame-detectable exactly as hoped — just not the "Annexes" **title** cartouche the original task specified. Declared conclusion for this file: same `⚠️ "rectangles présents mais aucun ne correspond à l'encart titre"` bucket as ASUP2AD5, but for a different reason (title text nearly absent here, vs. present-but-unframed there).

**Read across 2 files now tested**: neither contains the described "Annexes / Servitudes d'utilité publique / II. Utilisation..." title cartouche. But the mechanism itself (vector rect containing a dense, coherent text cluster) is validated once, on a legend box — encouraging for "frame detection works when a frame exists," independent of whether the *specific* title text shows up.

**Open threads**:
- Still haven't tested the vector-frame hypothesis on a file that actually contains the "Annexes" title text — `ASUP1_2025_12_19.pdf` (flagged previous entry) remains the best lead, not yet run.
- Legend-box detection (this session's actual positive result) could itself become a separate, useful "encart" sub-type worth its own rule, distinct from title-inset detection — not scoped/decided here.

---

## 2026-09-08b — scratch/ study: encart (title-inset) detector on ASUP2AD5 — vector-frame hypothesis rejected (0 commits, read-only, scratch/ only)

**Done**: `scratch/detect_encart_rect.py` (read-only, `get_drawings()` + `get_text("dict")`, no threshold coded) tests whether ASUP2AD5's assumed title inset ("Annexes", "Servitudes d'utilité publique", "II. Utilisation…", black frame/white fill) is isolable as a vector-drawn rectangle. **Finding, checked before running the full script**: the described anchor text does not exist anywhere on ASUP2AD5's page 1 at all (`get_text` has zero spans matching annexe/servitude/utilisation/énergie/circulation-aérienne beyond unrelated "SERVITUDES CONCERNANT..." labels) — this file is a single-page raster map mosaic (5 embedded images, up to 21480×15188px) with ~1670 plausible vector rectangles, but they're page borders and colored arrondissement/zone outlines, not a title cartouche. **Conclusion: ⚠️ "rectangles présents mais aucun ne correspond à l'encart titre"** — the top candidate by span-count is the whole-page border (200/200 spans), not a small title box.

**Lead for next step (not chased, out of scope for this task)**: `ASUP1_2025_12_19.pdf` (same folder, `Ressources/PLU bioclimatique/Annexes/Plans SUP/`) does contain 108 spans matching "Annexe(s)" — likely the actual legend/index sheet the task's description was drawn from, distinct from ASUP2AD5 (a specific geographic plan tile). The task said "commencer par ASUP2AD5" implying more files to follow; this is the natural next candidate.

**Open threads**:
- Vector-frame hypothesis is untested on a file that actually has the described cartouche — re-run `detect_encart_rect.py` against `ASUP1_2025_12_19.pdf` before concluding "frame detector doesn't work" vs. "wrong file was picked."
- If ASUP1 also has no matching frame, the fallback (text-anchor "Annexes" + proximity) becomes the working hypothesis for the "planche à encart" layout class — not yet validated either way.

---

## 2026-09-08 — scratch/ study: sommaire-tree extraction (REG1→REG2) + page-1 line-gap diagnostic (0 commits, read-only, scratch/ only)

**Done**: three disposable studies, no production code touched, no PDFs split/modified. (1) `scratch/extract_sommaire_tree.py`: outline extractor validated on REG1_MS1 (161 entries, 5 levels, two numbering systems, 100% anchor-check match after fixing an ALL-CAPS-wrap grouping bug and a `page_fin < page_debut` dense-page clamp bug). (2) Extended to REG2A1 (Tome 2, annex/table type): ran the REG1 script **unchanged** first — silent total failure (0 pages, 0 entries, no error) because REG2's header never says "SOMMAIRE" and its numbering vocabulary (`Annexe I :`, `1ère partie :`) shares zero regexes with REG1. Wrote `scratch/extract_sommaire_tree_reg2.py` (generalized TOC-page detection via body-content title search + line-shape continuation, instead of header-text match) — 12 entries, 3 levels, 100% anchor match. Full generic-vs-specific breakdown in `scratch/REG2_FINDINGS.md`. (3) `scratch/dump_page1_line_gaps.py`: read-only span-gap dump (page 1 only) across 4 PDFs to observe the `gap_y/font_size` ratio distribution ahead of a future title-extraction threshold — **no threshold coded, observation only**.

**Finding worth flagging**: `ASUP2AD5.pdf` (expected to be "the richest multi-level case") turned out to be a graphic map plan (servitudes/street-name labels scattered across a plan, not linear prose) — its page-1 span y-ordering is largely non-monotonic with reading order, producing mostly noisy negative ratios; it does **not** exhibit the "II. Utilisation… → ressources et équipements" wrap-vs-section pattern the task described. `PADD.pdf` and `OAP_BARTHOLOME_BRANCION.pdf` did show the expected two-cluster shape (small ratios ≈ [-0.85, 1.09] for wraps, one large outlier ≈ 15–25 for the real block break to the legal boilerplate line). `ANNAD1_2025_12_19.pdf` has only one transition, ratio -15.44, too little signal to judge either way.

**Open threads**:
- ASUP2AD5 needs a different diagnostic (or exclusion from the page-1-title heuristic entirely) — it's a plan/legend page, not a titled document; don't assume it validates the ratio threshold.
- The generalized TOC-detection rule from REG2 (body-content title search + shape-based continuation) is a candidate to replace REG1's header-text rule as the shared/generic version — untested against REG1 itself, flagged for a follow-up pass before promoting it.
- REG2A1's one heuristically-flagged "body text mis-shaped as TOC entry" (Annexe III, page 14) still needs a human call: keep as a tree node or drop.
- Threshold itself (ratio cutoff separating wrap from section-break) is intentionally undecided — next step is picking it with the domain expert once more PDFs are dumped, per the task's own instruction not to hardcode a decision here.

---

**Correction (2026-07-21):** the T6/T7 task labels below were briefly swapped in this file's narrative — a29296f (table_row scoping, `_annexe_route`, CH-06 13/13) is **T7**, 79adb0f (faithful Notion→JSON export script) is **T6**. Fixed here; git history/commit messages untouched.

## T6 — 2026-07-21 — Faithful Notion export replaces golden_dataset.json (1 commit, 0 re-ingest, 0 restart)

**Context**: legacy `golden_dataset.json` was hand-simplified and had invented content — confirmed by direct inspection: fabricated `DG_E_HAUTEUR.pdf` document expectation for UC-01, and (per the task's framing, consistent with UC-03's own real Notion text explicitly flagging it as "probablement une invention") a fabricated "H/2 min 6m" prospect rule. Wrote `scripts/export_golden_cases.py`: Notion API (`/v1/data_sources/{id}/query`, stdlib `urllib`, no new dependency) → strict fidelity mapping (verbatim text copy, closed enum maps that raise on drift instead of guessing, comma-split `articles_attendus`, empty→null/[]), consistency guard (refuses to export if any case is `fiabilite='Fabriqué - à refaire'` AND `validated=true`). 25 new unit tests (`tests/test_export_golden_cases.py`); full suite 225 passed.

**Wired `eval.py`**: `_normalize_expectations` now also accepts `articles_attendus`; `_print_summary` split into two separate tables+averages — certified (`validated==true`) vs. pending — never blended into one headline number. `run_eval` threads `validated` into each result dict.

**Smoke test** (replay of the 12 real Notion rows fetched this session via MCP tools — live `NOTION_API_KEY` not available in this environment, disclosed rather than faked): 12 cases exported (10 legacy IDs + 2 new: CH-02, CH-05; 0 dropped). **0/12 `validated==true`** — contradicts the task's own framing of "4 cases pending" (UC-04/UC-16/CH-02/CH-04): the live "Validé Charline" checkbox is unchecked for **all 12**, not just those 4. No score from this run is a certification. `eval/golden_export_diff.md`: most legacy questions were paraphrased away from Charline's actual wording; `expected_articles`/`articles_attendus` differs in 7/10 common cases.

**Kept `eval/golden_dataset_LEGACY.json`** (frozen pre-export copy) as a diff-only / relative-non-regression sentinel — documented in CLAUDE.md §3/§4 that the 80.0%/91.7% reference scores were never a certified/absolute measure, only a relative delta against a flawed yardstick.

**Open threads**:
- Live Notion export path is untested end-to-end (no `NOTION_API_KEY` here) — next session with a real key: run live once, diff against this replay-based export (should match modulo Notion page ids).
- Certification is a separate follow-up: needs the checkbox owner to review and check "Validé Charline" per-case in Notion before any score counts as certified.
- `expected_keywords` (legacy `_score_answer` input) has no analog in the new schema — degrades gracefully (vacuous 1.0) rather than being synthesized; a real answer-scoring path for the new schema is still open.

---

## T7 — 2026-07-21 — Scope table_row chunks: fixes 2/6 polluted cases + CH-06 exhaustiveness (1 commit, re-ingest, 0 restart)

**Audit before fix (T5's hypothesis was BM25-only)**: pulled FAISS/BM25 breakdowns for the polluted cases. UC-04's pollution ("hôtel" query surfacing Annexe X) had FAISS 0.62-0.72 (high) AND BM25 30-44 (high) — **both signals**, not BM25-only. Root cause: "hôtel" the lodging-use category (UG.1.3) vs. "hôtel particulier" the heritage-building term (Annexe X) — the embedding model doesn't disambiguate the polysemy either. Confirms the task's core diagnosis (table rows are a different retrieval class) but the mechanism is broader than hypothesized — adapted the fix accordingly (exclusion, not a BM25-specific tweak).

**Changes**: (1) `Chunk.chunk_type` field, `"table_row"` tagged on every `chunk_text_by_table` `_emit_row` output (genuine rows), `None` on preamble/fallback prose — even the first LS/BRS row (which bundles the section's intro paragraph by design, see T2) is correctly `table_row`. (2) `retriever.py`: `chunk_type == "table_row"` excluded from the default candidate pool at all 6 `allowed`-set construction sites (`search`/`search_weighted` × family_filter/unscoped/scoped-fetch_fn). (3) `_annexe_route()`: deterministic section-filtered lookup for a query that explicitly names an annexe's list (Annexe V/I/X — conjunctive keyword-stem detection, e.g. "emplacement"+"reserv"+"logement", never a partial match — see `_ANNEXE_ROUTES`), bypassing FAISS/BM25/RRF entirely; exhaustive (ignores `limit`) when an arrondissement token is present, matching document order. Guard: fires only when `family_filter` doesn't exclude `reglement_ecrit`, returns `None` (never `[]`) on no match — callers always fall through cleanly. 26 new tests (12 table_chunker + 14 `test_annexe_route.py`), 200 passed.

**Re-ingested** `reglement_ecrit`; `chunk_type` confirmed persisted (13,515 `table_row` / 728 prose). `eval --no-llm` on the certified reference: **80.0% → 80.0%, byte-identical** — no regression.

**Full 12-case re-run** (`eval/results/golden_v2_retrieval_20260720.json`), compared directly against T5:

| cas | T5 | T7 | verdict |
|---|---|---|---|
| UC-01 | ✗ | ✓ | **FIXED** |
| UC-05 | ✗ | ✓ | **FIXED** |
| UC-02/03/16, CH-04 | ✓ | ✓ | unchanged, no regression |
| CH-05 | ✓ | ✓ (via route) | route fires correctly, but only found 2 chunks — Annexe I's own row-detection was weaker in T2 (fell to char_fallback more than address_row), a pre-existing gap this task didn't touch |
| CH-06 | ✓ section-level, but 0-1/13 "1er" rows even at top_k=100 | ✓ **13/13 "1er" rows at every top_k (10/20/50/100)** | **primary target — fully fixed** |
| CH-03 | Annexe X (accidental pollution), UG.2.2.3 missed | UG.2.2.3 matched, Annexe X gone (route doesn't fire without "protégé"/"patrimoniale" wording — by design, not forced) | net improvement, documented trade-off |
| UC-04, CH-01, CH-02 | ✗ | ✗ **unchanged** | **different root cause — NOT table_row pollution** (verified: their hit lists are 100% prose post-fix, zero Annexe chunks, yet still miss the expected article — this is ordinary UG-sub-article ranking granularity, ~15-20 similarly-shaped codes competing for 6 slots) |

**Open threads** (both are new, separate follow-up candidates, per "one fix at a time"):
- UC-04/CH-01/CH-02's real cause (article-level ranking granularity among REG1's own prose, unrelated to table rows) is now cleanly isolated but unaddressed — needs its own audit, not a table_row-scoped fix.
- CH-05/Annexe I exhaustiveness depends on T2's row-detection quality for that specific annexe (weaker than Annexe V/X's) — a future table_chunker refinement, not a retriever-layer fix.

---

## 2026-07-20 — Corpus fact-checks + fresh golden-v2 runs: post-repair measurement (0 commits, read-only)

**Blocker hit and worked around**: Ollama's `/api/generate` hangs indefinitely for both `gemma3:4b` (expansion) and `ministral-3:8b` (synthesis) — confirmed via a direct `curl -m 60` test (0 bytes received), not just `backend_check`'s own "load failed: timed out". Matches the documented Vulkan/CUDA fix needing a reboot to persist (see memory `ollama_gpu_vulkan_fix`) — **not fixed here**, out of scope for a measurement-only task. Consequence: **no LLM synthesis this session** — "citation active" and "answer summary" columns could not be measured. Ran retrieval-only instead (`scripts/run_golden_v2_retrieval.py`, index loaded once, all 12 cases + CH-06 deep-dive in ~2 min vs. the ~150s/case cold-start the CLI subprocess path pays).

**Part A (verbatim REG1_MS1.pdf fact-checks)** — 2 of 5 hypotheses contradicted by the text:
1. No "figure FNE" anywhere (0 matches for "FNE"). Real prospect figure exists: Figure 6 (p.232, "Détermination du prospect* sur voie*").
2. **UG.3.1.2** (limites séparatives): baie-dependent, not a flat rule — 6m for baies de pièces principales (p.64), 3m for other baies, 3m for blank façades in retrait. **No H/2 formula anywhere in the document** (0 matches) — this PLU bioclimatique uses fixed metric distances, not H/2.
3. UG.1.4.1: **confirmed**, verbatim — "SPE... supérieure à 4 500 mètres carrés doit comprendre... une surface de plancher* destinée à l'Habitation supérieure à 10 %... avec un minimum de 500 mètres carrés" (p.45). SPE = "surface de plancher* liée à l'activité économique" (p.43). Directly validates UC-05's "sous le seuil" expectation (2000m² < 4500m²).
4. UG.3.1.1: **contradicted** — the voie-width threshold is **6 mètres**, not 15m (p.62: "voies* de largeur inférieure à 6 mètres..."). "15 mètres" does appear in REG1 but only for unrelated UG.3.3.1/3.3.2 dépassement contexts — don't conflate.
5. UG.3.3.1 (p.82-83): confirmed list — énergie renouvelable (+3m), protection solaire, acrotères, locaux techniques toiture végétalisée, sport (+5m), agriculture urbaine (+4m), garde-corps (1,20m), pare-vues (1,90m), souches/conduits (+1,50m), édicules circulation verticale (<3,50m×<4m, +1m ascenseur existant), signaux architecturaux (+15m sous conditions), pignon (+1,50m).

**Part B — 12 fresh runs, retrieval-only** (top_k=10, expand_query=OFF — expansion fails anyway given the Ollama blocker, disabling it avoids paying its timeout for a guaranteed failure; scoped_retrieval=True, slots reglement_ecrit=6/annexes=2/oap=1/padd=1, unchanged). Results: `eval/results/golden_v2_retrieval_20260720.json`.

| cas | article_ok | verdict | note |
|---|---|---|---|
| UC-02, UC-03, UC-16, CH-04 | ✓ | OK | clean, 0 Annexe-noise in the 6 reglement_ecrit slots |
| CH-05 | ✓ | OK | Annexe I correctly retrieved — **confirms T3's RP_HOTEL_DIEU finding empirically**: 0/12 cases ever surface rapport_presentation (0 slots, structural) |
| CH-06 | ✓ (section-level) | [S] | see deep-dive below |
| UC-01, UC-04, UC-05, CH-01, CH-02 | ✗ | [S] | see crowding finding |
| CH-03 | partial (Annexe X only, UG.2.2.3 missed) | [S] | UGSU.2.2.3 (wrong zone) surfaced instead |

**Major new finding (not a fix — flagging for a dedicated follow-up)**: every failing/partial case has ≥2/6 reglement_ecrit slots consumed by Annexe V/X row chunks; **UC-04 has 6/6** (zero UG.1.3 prose reached the family budget at all). Every passing UG.x-prose case has 0/6 Annexe-noise. This correlates cleanly with T2's fix — REG2A1_MS1.pdf's row-level re-chunking took Annexe V from ~270 to 817 chunks and REG2A10 from ~3.5k to ~5.75k, hugely increasing the candidate pool competing for reglement_ecrit's fixed 6 slots. **Labeled [S] not [R]**: no direct pre-T2 baseline exists for these v2 questions (they're new), so this is a strong, quantified correlation, not a proven regression — needs its own before/after audit as the next task, not asserted here.

**CH-06 deep-dive (isolates ranking from row-integrity, as requested)**: of 13 genuine "1er"-starting Annexe V rows confirmed present in `chunks.json` (verified directly, independent of retrieval), **0-1 surface in results at top_k=10/20/50/100** (family-scoped) — the 1 that appears is the intro-paragraph chunk that happens to bundle the first "1er" row, not a dedicated hit. Root-cause confirmation: **row-integrity is fixed (T2), rows exist correctly** — this is purely a ranking/breadth gap, same class as the already-open 07-16 OAP perimeter-filtering issue. Embedding+BM25 scoring doesn't discriminate "1er" among ~817 structurally near-identical rows from other arrondissements.

**Open threads** (both need dedicated future sessions, per "one fix at a time"):
- Annexe-crowding: does T2's finer chunking genuinely regress UG-article retrieval, or is this pre-existing? Needs a controlled before/after (can't do post-hoc, the old index is gone).
- CH-06/arrondissement-exhaustiveness: same open perimeter-filtering gap as 07-16 — a query needs an explicit arrondissement-boost mechanism to beat the volume of same-shaped sibling rows.

---

## 2026-07-20 — REG1 sommaire parse + concept→article mapping proposal (1 commit, read-only, 0 restart)

**Context**: prerequisite for a future routing/query-expansion "hinge" that maps architect-facing concepts to article codes. Read-only research task — no pipeline code touched, nothing wired in.

**Part A**: `scripts/parse_reg1_sommaire.py` parses REG1_MS1.pdf's front-matter SOMMAIRE (pages 2-5, confirmed by direct inspection — page 6 is where real content starts) into `{article_code, theme, page}`. First pass had a page-boundary bug: 3 entries whose title wrapped across a page break picked up the *next* page's running-footer year ("2025") as their page number instead of the real one, because the "last number in the segment" heuristic doesn't distinguish a genuine trailing page number from page-boundary noise bleeding into the same regex segment — fixed by anchoring on the number immediately after the *first* dot-leader (or, for the one entry with a single stray dot instead of a leader, the *first* number in the segment) rather than the last number anywhere in it. Verified via page-monotonicity check (0 violations after the fix, was 3 before) and a manual sample cross-check against the task's own worked example (UGSU.3.2 → page 139, matched exactly). Output: `eval/ontology/reg1_sommaire.json` — 119 entries, 119 distinct codes. Coverage diff vs the indexer's live whitelist (`build_article_whitelist()`, 340 codes): 101 in both; 18 "only in sommaire" are all 2-level parent codes (e.g. `UG.2`, `N.1`) that never appear as a chunk's own section since real content always resolves to a more specific child; 239 "only in whitelist" are finer sub-articles (e.g. `N.1.2.1`, `UG.2.2.3`) one level deeper than REG1's own summary lists — the summary's granularity genuinely caps below some real content, a fact Part B had to work around (see below).

**Part B**: `eval/ontology/concept_article_mapping_proposal.csv` — 11 concept rows (10 glossary terms from the task prompt, with "emplacements réservés" split into 2 rows since it turned out to name two unrelated regulatory mechanisms — see below), each proposing article code(s) chosen **only** from Part A's 119-code closed vocabulary (verified programmatically: 0 codes outside that set). Cross-validated every mapping I could against `golden_dataset.json`'s already-Charline-vetted `expected_articles` (UC-02/03/04/05/16, CH-01/03) rather than guessing blind — 6 of 11 rows are gold-confirmed (high confidence), the rest are title-pattern inference (medium) or genuinely ambiguous (low, flagged explicitly for Charline's call).

**Non-obvious finding surfaced in the proposal**: "emplacements réservés" is used in this corpus for two unrelated mechanisms — housing reservations (Annexe V, the CH-06 case, governed by `UG.1.5.2` nested under `UG.1.5 Mixité sociale`) vs. equipment/facility reservations (`UG.1.6`, present near-identically in all 4 zones). A routing hinge that doesn't disambiguate these would misroute one or the other. Also: golden CH-03's real answer needs `UG.2.2.3`, one level deeper than REG1's summary shows for the `UG.2` (aspect extérieur) family — the proposal names the closest available parent (`UG.2.2`) and says so explicitly rather than silently rounding to a plausible-looking but wrong-precision code.

**Not done (out of scope, per the task)**: no pipeline wiring, no re-ingest, no eval run — this is Charline-facing review material, not a shipped change.

**Open threads**:
- Part B used the concept list given inline in the task prompt (10 terms) — the task says "I will paste the Glossary terms," implying a fuller list may follow; this proposal should be extended, not restarted, when/if that arrives.
- All "medium"/"low" confidence rows (aspect extérieur/volets, couverture/toiture, emplacements réservés-logement, secteurs particuliers) need Charline's sign-off before any future wiring — flagged in the CSV's own rationale column, not just here.

---

## 2026-07-20 — index hygiene: ANN2A exclusion, RP audit, referentiel regen (1 commit, re-ingest, 0 restart)

**Context**: 3-item hygiene task. Audited each claim before acting (two of three didn't hold as stated — see below).

**1. ANN2A_2025_12_19.pdf exclusion — REAL, fixed.** Direct chunk inspection confirmed: all 4 pre-fix chunks were nothing but the same repeated title banner ("ZONAGE D'ASSAINISSEMENT DES EAUX USÉES ET ZONAGE PLUVIAL DE LA VILLE DE PARIS...") — a map/plate with no extractable substantive text. Added `validity: excluded` to `corpus_mapping.yaml` (a new value alongside `current`/`superseded` — reusing `superseded` would have been factually wrong per that value's own documented meaning, "a replacement exists under another name," which isn't true here). Generalized the 3 call sites that special-cased `== "superseded"` (`indexer.build_index`, `check.check_coverage`) to `!= "current"` instead, so any future non-indexable-for-a-new-reason file doesn't need a 4th special case. 3 new tests.

**2. RP_20251017_MC1_HOTEL_DIEU.pdf pollution — claim NOT reproducible, no change made.** Confirmed the file genuinely contains UG.x.y codes in body text (49/131 chunks — it's a modification dossier quoting affected articles), but: (a) "CH-05" doesn't exist in `golden_dataset.json` or anywhere else in this repo; (b) `rapport_presentation` already has **0** retrieval slots under `scoped_retrieval` (the default) — `scoped_retrieval_merge` skips a 0-slot family entirely at fetch time, so this file structurally cannot surface unscoped, confirmed by reading `retriever.py` and by live testing; (c) no session log (`data/sessions/*.jsonl`) has ever mentioned this filename. Task's own option (b) — "keep it addressable only when explicitly asked" — is already exactly today's behavior (`--family rapport_presentation` bypasses slot allocation entirely and finds it fine, confirmed live). The only real gap is `--no-scoped-retrieval` (an explicit debug flag, not a default), too narrow to justify option (a)'s heavier tagging/ranking work. Least invasive = no code change; documented here instead.

**3. Referentiel drift — the named files were NOT stale (false premise), but regenerating found real drift elsewhere.** REG1.pdf/REG2A1.pdf/REG2A10_1DE2.pdf are still on disk (not deleted) and `referentiel.yaml` already correctly shows `validity: superseded, chunk_count: 0` for all three — zero drift there. Running `regenerate_files()` anyway (diffed before writing) found the REAL drift: stale `chunk_count`s for the 3 files T2's table-chunker touched (274→7826, 1636→3205, 1862→2546 — referentiel.yaml hadn't been regenerated since that re-ingest) plus ANN2A's validity update from this session. Regenerated and saved — clean 8-line diff, `git diff referentiel.yaml` reviewed before committing.

**4. Re-ingested** `annexes` family (`--rebuild --family annexes`, scoped — REG*/reglement_ecrit untouched). `aria-rag check`: 0 FAIL / 3 WARN (all pre-existing categories) / 7 PASS. Confirmed live: 0 ANN2A chunks in `chunks.json` (was 4). Full suite 184 passed (was 182). `eval --no-llm`: 80.0% → 80.0%, byte-identical (`results_20260720_142107_8ad6bab2.json` → `results_20260720_173438_568c348f.json`) — expected, ANN2A/RP were never golden-case-relevant.

**Open threads**: none new. Item 2's narrow residual (`--no-scoped-retrieval` bypass) is a known, accepted, debug-only gap — not tracked as a TODO since fixing it would be premature optimization for an unobserved risk.

---

## 2026-07-20 — structure-aware table chunker: CH-06 root-cause fix (1 commit, re-ingest, 0 restart)

**Context**: file-level diagnostic (Notion, Ontologie documentaire) found `reglement_ecrit` is structurally heterogeneous — `REG1_MS1.pdf` is article prose (existing `chunk_text_by_article` fits), but `REG2A1_MS1.pdf` (Annexes I-IX) and `REG2A10_*.pdf` (Annexe X) are compact tables. The article chunker's char-window fallback (no article headers to split on) cut table rows apart at arbitrary 1200-char boundaries — CH-06's root cause (2/17 addresses retrieved from Annexe V instead of ~17).

**Fix**: new `chunk_text_by_table` (`indexer.py`) dispatched per-file via `TABLE_CHUNKED_FILES = {REG2A1_MS1.pdf, REG2A10_1DE2_MS1.pdf, REG2A10_2DE2_MS1.pdf}`. Detects real (non-ToC, non-cross-reference) annexe headers, merges consecutive byte-identical repeats (running page headers) into one span, then row-splits each span by whichever signal explains the MOST of it: LS/BRS reservation code (Annexe III/V, trusted outright — near-zero false-positive), else `max(address-line count, BP/EPP entry-start count)` (Annexe VI-IX vs Annexe X) — this max-of-signals tiebreak was necessary because a single incidental address-shaped line inside Annexe X's free-text Motivation prose (e.g. a wrapped continuation line) was hijacking branch selection under a fixed-priority design during dry-run testing. Annexe I/II/IV (no row-boundary signal found) fall back to a chunk_size-bounded char split, still correctly confined to their own annexe span (fixes cross-annexe mislabeling either way, just not row-granular — one fix at a time). A genuine table row is kept whole even past `chunk_size` (never split, per the task's own requirement); preamble/fallback prose is still safety-bounded (`_emit_bounded`) so one stray match can't turn everything before it into an oversized chunk — an early design bug caught and fixed during dry-run validation, before touching the real corpus.

**check.py update**: `check_size_cap` gained a narrow, documented exception (`TABLE_ROW_MAX_LEN=8000`) for `TABLE_CHUNKED_FILES` — Annexe X's Motivation descriptions legitimately run up to ~5.2k chars for one protected building (largest observed in this corpus), and the whole point of the fix is never splitting that row. `checks/known_manifest_desync.json` gained 3 new entries (REG2A1_MS1.pdf, both REG2A10_*.pdf) — same known, already-documented dedup shape (manifest's pre-dedup per-file count vs chunks.json's post-dedup count) as the 25 pre-existing entries, not a new bug class. 12 new tests (`test_table_chunker.py` ×10, `test_check.py` ×2); full suite 182 passed (was 170).

**Re-ingested** `reglement_ecrit` (`--rebuild --family reglement_ecrit`): 4438 → 16 776 chunks for the family (REG2A1_MS1.pdf 274→7826, REG2A10_1DE2 1636→3205, REG2A10_2DE2 1862→2546, REG1_MS1 unchanged at 666), reflecting genuine row-level granularity replacing 1200-char windows. `aria-rag check`: 6 PASS / 3 WARN (all pre-existing accepted categories: encoding, fragment floor, referentiel coverage) / 0 FAIL after the desync allowlist update — size cap and annexe section plausibility both PASS (were previously the two invariants a naive fix could most easily break).

**Verified live** (`aria-rag ask --debug --no-llm`, post re-ingest): Annexe V hit for the CH-06 question is now a single clean row — `1er 15 rue d'Argenteuil LS 100-100` — not the old destroyed multi-row blob. Scoped structural audit (dry-run, pre-ingest) confirmed 13/13 real "1er" Annexe V rows correctly preserved as complete, non-fragmented chunks (~20 addresses across those rows, matching the task's "~17" estimate) — direct fix confirmation, independent of the coarse eval metric below.

**`eval --no-llm` before/after: 80.0% → 80.0%, byte-identical per-case** (results: `results_20260720_131713_39f41c58.json` → `results_20260720_142107_8ad6bab2.json`). Expected, not a null result: the golden retrieval score is a coarse file/section-substring match (`_hit_satisfies`) — CH-06/CH-03 were already 100% at that granularity pre-fix (the *file* was being retrieved, just with its content destroyed inside), so this metric can't show a row-level integrity fix either way. Non-regression confirmed; real signal is the live verification above.

**Open threads**:
- Exhaustive single-query retrieval of ALL ~17 addresses for one arrondissement is NOT what this fix solves — a `--family reglement_ecrit --top-k 20` query surfaced only 1 genuine "1er" row alongside many other arrondissements' structurally-similar rows (semantic+BM25 ranking has no arrondissement-exclusivity signal). This is a retrieval breadth/ranking concern, same class as the already-open 07-16 OAP perimeter-filtering gap — root cause (row destruction) is fixed; exhaustiveness-via-one-query is separate and still open.
- Fragment-floor WARN (46 chunks, pre-existing accepted category) includes a handful of new degenerate rows from this fix — bare arrondissement-digit lines (`[Section: Annexe VI]\n1`) and one broken multi-line BP reference (`[Section: Annexe X]\nBP\net`) — where a row's address wrapped across lines in a shape the simpler Annexe VI-IX per-line splitter doesn't handle as gracefully as the LS/BRS and BP/EPP paths. WARN-only, small (<0.2% of corpus), not chased further this session.
- `eval`'s default `--timeout 120` is too short for this machine's cold embedding-model load (~150s+ per `ask` subprocess) — every case timed out at the default; used `--timeout 300` for both before/after runs here. Environment-specific, unrelated to CH-06, not fixed.
- Annexe I/II/IV (REG2A1_MS1.pdf) remain char-window-split (no row-boundary signal implemented for their shape) — correctly section-bounded now, not row-granular. Only worth a follow-up if a future golden case needs them.

---

## 2026-07-20 — section-field staleness check: premise false, no re-index run (read-only, 0 commits)

**Context**: incoming task claimed `data/index/chunks.json` predates the article-section-extraction commit, with only `{chunk_id, source_path, doc_family, content}` persisted, and asked for a full corpus re-ingestion to populate `section`.

**Audit before fix**: checked the live index first. `section` is already populated — sampled 10 random `REG1_MS1.pdf` chunks, 10/10 valid codes (`UGSU.3.2.6`, `UG.4.3.5`, `N.7.2`, etc.). Corpus-wide: 4436/4438 `reglement_ecrit` chunks have a non-null `section` (the 2 nulls are both in `REG2A10_*.pdf`, plausibly legitimate unsectioned front matter — not investigated further, low priority). `git log -- src/aria_rag/indexer.py` confirms `3c32025` (scoped section-mislabel fix) already landed, and the 2026-07-14 SESSION_STATE entry already documents a `reglement_ecrit`-scoped re-ingestion *after* that exact commit (`check --strict` → 0 FAIL, `eval --no-llm` 80.0%/86.7%).

**Verdict: premise was stale, task not executed.** The index already reflects current `indexer.py` behavior — no re-ingestion needed. Running a full corpus re-embed (16.8k chunks, touches FAISS/BM25/manifest) on a false premise would be a real-cost, mostly-irreversible-in-time operation with no defect behind it. Declined per the project's own "audit before fix" convention.

**Open threads**: the 2 null-`section` chunks in `REG2A10_*.pdf` are unexplained — worth a quick look if anyone's already touching that file, not worth a dedicated session on its own.

---

## 2026-07-16 — OAP slot count measurement: verdict "don't apply" (read-only, 0 commits)

**Context**: follow-up to the 735984b perimeter investigation. `DEFAULT_FAMILY_SLOTS["oap"]=1` — does raising it improve OAP coverage without regressing the certified reference?

**1. Budget model**: `DEFAULT_FAMILY_SLOTS` = `{reglement_ecrit:6, rapport_presentation:0, annexes:2, oap:1, padd:1}`, sums to **exactly `top_k=10`** (`config.py`'s own comment confirms this is deliberate). `scoped_retrieval_merge` (`retriever.py:132`) allocates each family its slot count as a primary guarantee, redistributes any family's *shortfall* to others' next-best candidates, then hard-truncates the final tier-interleaved list to `total_k` via `merged[:total_k]`. **Mechanical finding**: when the slots dict sums to more than `top_k` (raising oap without lowering anything else), the truncation silently drops the *deepest* rank-tier entries — which are always `reglement_ecrit`'s (the only family allocated beyond rank 2). So oap=2 costs `reglement_ecrit` exactly 1 of its 6 slots; oap=3 costs it 2. **Verdict for item 1: displaces, never additive** — confirmed both by reading the algorithm and empirically (`reglement_ecrit` per-case count measured at 6/5/4 for oap=1/2/3, every single golden case).

**2. Golden-set measurement** (standalone harness, index loaded once, reused `eval.py`'s own scoring functions — not the CLI subprocess path, to avoid reloading the embedding model 30×; results not written to `eval/results/` since this is a scratch measurement, not a certified run): **oap=1: 86.7%, oap=2: 86.7% (byte-identical per-case scores to oap=1), oap=3: 81.7% — REGRESSION on CH-03 (0.5→0.0, loses both `UG.2.2.3` and `Annexe X`)**. oap=1's 86.7% matches the already-documented 07-14 reference exactly (methodology cross-check passed). No other case moved at any oap value.

**3. OAP-targeted coverage** (3 new questions targeting Bercy-Charenton/Portes-Est/Paris-Rive-Gauche, not in the golden set): **decisive negative result**. Portes-Est and Rive-Gauche already surfaced their correct OAP file at oap=1 — raising the slot added zero benefit, only extra wrong-sector filler chunks. Bercy-Charenton was wrong at oap=1 **and stayed wrong at oap=2** (`OAP_BEDIER_OUDINE.pdf` + `OAP_PARIS_RIVE_GAUCHE.pdf`, neither correct) — only fixed at oap=3, and even then bundled with 2 more off-sector chunks in the same 3-slot allocation (net: *more* pollution, not less, alongside the correct file).

**4. Verdict: DO NOT APPLY. Left at oap=1.** oap=2 is reference-neutral but delivers **zero** OAP-coverage improvement (0/1 target-file hits, same failure as oap=1) — no upside to justify the change. oap=3 does surface the one previously-missing target file but at the cost of a real golden-case regression (CH-03) and doesn't reduce pollution, it triples it. This empirically confirms 735984b's own conclusion: **more slots is not the fix; perimeter filtering is.** No code touched, no restart.

**Open threads**: none new — this closes the "should we just raise the slot" side-question the 735984b investigation left open, and points back at the same perimeter-filtering design task (still blocked on the missing arrondissement input at the `AskRequest` layer, per that entry).

---

## 2026-07-16 — OAP perimeter-filtering investigation (read-only, 0 commits)

**Context**: Charline — when zone/arrondissement is known, retrieval surfaces OAP from unrelated sectors (Bercy-Charenton, Portes, etc.), polluting the answer. Goal: determine IF/HOW to filter before designing a fix. (User's message arrived truncated mid-sentence at step 2a — proceeded on the clear parts and a reasonable read of the rest, confirmed correct once the full message landed.)

**1. Inventory**: 19 distinct OAP files, 292 chunks total, split by folder: **13 `Sectorielles/`** (perimeter-specific, 5–31 chunks each) + **6 `Thématiques/`** (citywide, 16–34 chunks each). `corpus_mapping.yaml` has one blanket prefix rule for all 19 (`family: oap, norm_level: local, city: paris`) — no sector/arrondissement field anywhere in that schema. `referentiel.yaml` only adds folder-level `piece_id`s (`oap-sectorielles`/`oap-thematiques`), same granularity, still no per-file field.

**2. Perimeter signal — verdict: case (b), reliably in the text, not (c).** Every one of the 13 Sectorielles OAPs' first chunk has the identical extractable pattern: `Secteur « Name »\n(Xe arrondissement)` — confirmed 13/13 via regex, zero misses. None of the 6 Thématiques OAPs mention an arrondissement in their first chunk (citywide, as expected). **No hand-authored table needed** — the mapping is corpus-derivable.

**3. Granularity**: OAP perimeter = **named sub-arrondissement secteur**, not arrondissement itself (e.g. "Bartholomé-Brancion", "Olympiades / Villa d'Este-Place de Vénétie"). Arrondissement is metadata *about* the secteur, and it's **often multi-valued**: Maine-Montparnasse = 6e+14e+15e (3), Paris Nord Est = 18e+19e (2), Portes de l'Est parisien = 12e+20e (2); most others are single. Confirms the task's premise: zone (UG, city-wide) can't filter OAP — arrondissement/secteur is the only usable key, and it's 1-to-many from OAP to arrondissement.

**Non-obvious finding**: `retriever.py` already has arrondissement-extraction machinery (`_ARRONDISSEMENT_NUMERIC`/`_ARRONDISSEMENT_WORD`/`extract_discriminating_tokens`, built for Annexe-V address-table rows) and it's already invoked over every family's fetch pool via `_apply_lexical_boost` (`lexical_boost_factor=2.0` by default, always on). **It does not currently help here** — empirically verified the line-start regex (`^\s*12e\b`) does not match the OAP title's `(12e arrondissement)` format (leading parenthesis breaks the anchor). Also: `DEFAULT_FAMILY_SLOTS["oap"] = 1` — only **one** OAP chunk is fetched per query total, so "pollution" isn't volume, it's a perimeter-blind lottery across 19 files for that single slot.

**4. Filter options** (sketched, not built):
- **(A) Hard exclude at retrieval** — extend the existing `allowed: set[int]` index-restriction idiom (already used identically for `family_filter`) with a second filter: intersect the OAP `fam_allowed` set with chunks from OAP files whose arrondissement(s) match the query's. Cleanest guarantee (wrong-sector OAP literally never reaches the 1 slot). Cost: needs the query's arrondissement (see below) + a filename→arrondissement(s) lookup. **Can be a static Python dict, derived from the 13-file regex scan above — no `Chunk` schema change, no re-ingest required**, if keyed by filename rather than added as a per-chunk field. Doesn't touch the certified 80%/91.7% reference as long as it's a no-op when no arrondissement is known (same non-breaking pattern `family_filter` already follows).
- **(B) Extend the existing boost** — fix `_apply_lexical_boost`'s regex to also match `(Xe arrondissement)`, reusing the machinery that already runs on every OAP fetch. Cheapest change, but it's a *soft* multiplicative nudge, not a hard exclude — doesn't guarantee an unrelated sector never wins the single slot, only makes it less likely. Same no-re-ingest, no-schema-change profile as (A).
- **(C) Proper long-term metadata** — add arrondissement as a real `Chunk`/`referentiel.yaml` field (parallel to `norm_level`/`city`). More correct, matches the codebase's existing config-driven conventions, but **does** need a re-ingest (of `oap` family only, following the 07-14 scoped-rebuild precedent) and a schema change. Not needed for a first fix.
- **Blocking gap, all options**: `AskRequest` (`api.py`) has **no arrondissement/zone field at all** — only `question: str`. The architect's zone/arrondissement, if given, is buried in free text. Options A/B need something to filter *against*; today nothing extracts it into a usable value at the `/ask` layer (the retriever's extraction function exists but is only wired into the boost path, not exposed as a value the API could act on directly). **This is the actual precondition for any of the above** — flagging as the real next question for Charline/Anna, not a small detail.

**Open threads**:
- Whether to solve the missing-arrondissement-input gap via a new structured `AskRequest` field (needs a demo-side change, outside this repo) vs. reusing/exposing `retriever.py`'s existing free-text extraction at the API layer (no demo change, but heuristic — same "1er janvier" false-positive risk the boost regex already guards against) is an open design choice, not resolved here.
- No corpus-wide validation that EVERY OAP's arrondissement is truly static per file (i.e., no sub-file arrondissement mixing within a single Sectorielle PDF) — the 13/13 first-chunk match is strong evidence but each file's full text wasn't checked chunk-by-chunk for a stray second-arrondissement mention.

---

## 2026-07-16 — citation truncation fix: send full chunk text (1 commit, restart)

**Fix** (`960f906`'s audit → `6a4377a`): removed `textwrap.shorten(..., width=500, placeholder="...")` at both sites the audit found — `api.py`'s `Citation.excerpt` (the live `/ask` response) and `cli.py:246`'s `format_hits` (`--debug`'s "Retrieved passages" block) — now both send `hit.content`/`h.content` directly. Small, targeted: no other logic touched.

**Sanity check**: `Citation.excerpt` is a plain pydantic `str`, no `max_length` constraint; no test asserts a bounded excerpt length; `sessions.py` doesn't store excerpt/content at all (unaffected); `_FEEDBACK_FIELD_MAX_LEN` is a separate, unrelated cap on `/feedback` fields. No demo code in this repo to check directly — flagged as the one thing that couldn't be verified locally, same caveat as the `expand_query` payload question from 07-14.

**Verified live**: killed the stale server (still on `960f906`), restarted on `6a4377a`, `/health` confirmed. Live `/ask` call (UC-02's question, 10 hits): **all 10 citations carry the full chunk content, byte-for-byte** (9/10 matched an exact `(source, section, page)` chunk directly; the 10th had 3 duplicate-labeled `N.7.2` candidates at that key — one of the three matched byte-for-byte, confirming this is a pre-existing duplicate-section-label artifact, not a new truncation bug). Sample lengths, before → after: UG.3.1.1 chunk 1200 chars, excerpt was ~498 → now **1200**; UG.3.2.1 1200 → **1200**; UG.2.2.3 1153 → **1153**. `eval --no-llm`: **80.0%**, unchanged. Full suite: 170 passed.

**Note**: this closes the [API] half of the 07-16 audit only. Long articles split across multiple chunks (UG.3.1.1 = 7 chunks, UG.3.2.1 = 3 chunks, per the audit) still surface as separate, independently-full-length citations rather than one stitched article — expected, not a regression, and still the open [SPLIT] design task below.

**Open threads** (unchanged from the audit entry):
- No corpus-wide chunks-per-article census run — still only the 3 audited samples. Needed to scope the [SPLIT] stitching fix.
- The `marker_index`/`[N]`-to-citation 1:1 mapping is a real design constraint for any future stitching fix — still unresolved.
- Whether the Figma demo renders a long `excerpt` string acceptably (line wrapping, card height) is unverified — no demo code in this repo to check.

---

## 2026-07-16 — citation-truncation audit (read-only, 0 commits)

**Context**: Charline — "citations trop courtes vs chunks par section — pourquoi si court alors que les chunks sont par section ?" She expects a citation to show the whole article; sees a snippet. Audited where the truncation happens before deciding any fix (none applied — read-only).

**Verdict: [API] + [SPLIT] combined, [DISPLAY] ruled out.**

**[API] confirmed**: `api.py:388`, `Citation.excerpt=textwrap.shorten(h.content, width=500, placeholder="...")` — the live `/ask` HTTP response itself caps every citation at ~500 chars, regardless of chunk size. Measured on 3 sample `reglement_ecrit` articles (`data/index/chunks.json`): UG.3.1.1 chunk full_len=1200 → excerpt=498; UG.3.2.1 full_len=1200 → excerpt=496; UG.2.2.3 full_len=1153 → excerpt=484. This is baked into the API payload before the demo ever renders it — not a display-side truncation. The identical `textwrap.shorten(..., width=500)` call is duplicated in `cli.py:246`'s `format_hits` (CLI's "Retrieved passages" block) — same truncation, second call site.

**[SPLIT] confirmed, and likely the dominant effect**: even the full 1200-char chunk isn't "the full article" for any article article-aware chunking split across multiple chunks under the same `section` label. UG.3.1.1 = **7 chunks** (pages 61–63, ~7200 chars total); UG.3.2.1 = **3 chunks** (pages 69–70, ~3200 chars); UG.2.2.3 = **1 chunk** (1153 chars, fits whole — not every article splits). Live proof via a real retrieval (`aria-rag ask --no-llm --debug`, UC-02's question): 2 of the top-10 hits are both `section=UG.3.1.1` but different page ranges (61–62 vs 63) — retrieval already surfaces multiple fragments of the same article as **separate, independently-truncated citations**, and neither fragment includes the article's own opening/header text (both start mid-sentence).

**[DISPLAY] ruled out, correcting the task's framing**: the session log (`sessions.py`'s `_build_entry`) stores **no content/excerpt field at all** for hits — nothing to abbreviate. The "~200 chars" figure the task referenced is a different code path: `cli.py:481`'s `ask --debug` per-hit printout (`hit.content[:200]`), which feeds `eval.py`'s `raw_passages` in fidelity-grid results (already documented as a known limitation in `eval/fidelity_method.md`) — a diagnostic-only capture for the eval harness, never in the live demo's request/response path.

**Recommended fix (not applied)**: two layers, both needed for Charline's actual expectation (a full, coherent article) to be met — (a) quick: raise/remove the 500-char cap in `Citation.excerpt`, cheap but only closes the API-layer gap; (b) structural: when building citations, detect sibling chunks sharing `(source_path, section)` among the hits and stitch/merge them into one citation per article instead of N independently-truncated fragments — this interacts with the `marker_index`/`[N]`-to-citation 1:1 mapping (`api.py`'s citations list) and needs design input on how a merged citation reports its marker index.

**Open threads**:
- No article-chunk-count census run corpus-wide — only 3 samples audited. A full histogram (chunks-per-`(source_path,section)` across all of `reglement_ecrit`) would show how common multi-chunk articles are before scoping the (b) fix.
- The `marker_index` 1:1 hit-to-citation mapping is a real design constraint for (b) — not resolved here, flagging for whoever picks up the fix.

---

## 2026-07-16 — synthesis prompt overhaul: contradiction + zone/exceptions + tone (1 commit, restart)

**Context**: Charline's 07-13 user-story session validated retrieval but flagged three coupled defects, all in `SYSTEM_PROMPT` (`llm.py`) — fixed in one coherent rewrite since they share the same prompt surface. (1) **Self-contradiction** (worst): the anti-fabrication clause over-fired — an answer would cite a relevant passage and then declare "no information" for the same point in the same answer. Reproduced live pre-fix on the "volets" question (`session_2026-07-15_94b05ad4.jsonl`): cited OAP_CONSTRUCTION's "volets roulants à lames orientables" `[4]`, then concluded "le contexte ne permet pas de répondre clairement." (2) **Answer shape**: when no specific rule exists, the tool gave a curt refusal instead of the zone's general rule + exceptions to verify. (3) **Tone**: "trop sec, trop court, pas assez documenté" — needed more grounded citation, never more outside knowledge.

**Fix**: rewrote `SYSTEM_PROMPT` with an explicit three-case distinction (a: rule found → cite it; b: partial/connected elements found → give them + flag the gap; c: nothing relevant → say so) with an absolute rule that (a)/(b) can never collapse into (c) if a citation was made. Added zone-general-rule-then-exceptions structure (zone is user input, never inferred from an address) and an explicit "documented = cite more of the context, never add outside knowledge" instruction. Kept the grounding contract (context-only, `[N]` markers) unchanged.

**Measured** (14 cases: 10 golden + 3 adversarial + `CHECK-VOLETS`, an ad hoc case added to `scripts/run_fidelity_grid.py` reproducing the volets contradiction — not promoted into `golden_dataset.json`/`adversarial_dataset.json` pending Charline/Anna sign-off; script also updated to default `top_k=10`/`expand_query=True` matching production, was `top_k=8`/no-expansion): honest before/after via `git stash` (measured the pre-fix prompt fresh rather than reusing the stale 07-11 fidelity run, since corpus/config changed since then) — golden `answer_score` **71% → 84%**, retrieval unchanged at **87%** (confirms the gain is synthesis-side). `CHECK-VOLETS`: contradiction gone — fixed answer consistently frames the OAP passage as a non-binding "case (b)" element, never claims absence of information after citing it (verified byte-identical on the live restarted server too). ADV-B (Tokyo)/ADV-C (fake street) still refuse honestly, no invented values — the relaxed contradiction rule didn't reopen confabulation. `eval --no-llm`: **80.0%**, byte-identical to the documented baseline (retrieval untouched, as expected for a synthesis-only change). One iteration was sufficient — no second pass needed.

**Minor flagged finding (not fixed, not blocking)**: UC-04's fixed answer dropped a legitimate hedge the baseline had (uncertainty over whether the 8e arrondissement falls in the secteur d'encadrement des hébergements touristiques), stating the interdiction applies "y compris le 8e arrondissement" more flatly. Not a fabrication (the general-zone rule is genuinely UG-wide), but a small loss of epistemic caution — worth watching if it recurs on other cases.

**Verified live**: killed two stale `aria-rag serve` processes (PIDs 17836/14740, running since 15:07-13 18:19, predating this session's fix), restarted, `/health` confirmed `git_commit: "960f906"` and `gpu_verified: true` on both models. Smoke-tested the exact volets question end-to-end: `expand_query_requested: true`, `synthesis_model_was_resident: true`, answer byte-identical to the fidelity grid's fixed-prompt run.

**Open threads**:
- `CHECK-VOLETS` is a candidate for promotion to `golden_dataset.json` (or `adversarial_dataset.json`, as a fourth "partial-grounding honesty" case) — needs Charline/Anna's call, same as the CH-03 `expected_articles` question from 07-14.
- The UC-04 hedge-loss (see above) is a single-case observation, not chased further this session — flag if a pattern emerges across more cases.
- All prior open threads (article-side ToC bug, CH-03 golden-case field, 91.7% reference staleness, Figma demo's `expand_query` payload) are unchanged from 07-14, still open.

---

## 2026-07-14 — section-mislabel fix + expansion-on-by-default (1 commit, re-ingest, restart)

**Part 1 — section mislabel**: root cause confirmed: `REG1_MS1.pdf` p.15-16 has a genuine bulleted paragraph enumerating all ten Tome-2 annexes by name (introducing "voici les annexes suivantes") — each bullet matches `_ANNEXE_HEADER`'s real-header pattern, and the LAST one ("Annexe X") was winning the bisect section-lookup for ~25 pages of real content (Partie 1 + the Définitions glossary), not a casing bug. Fixed via `_drop_enumeration_runs` (3+ distinct annexe titles within 400 chars = enumeration, not a header) + a "du tome" cross-reference exclusion, both **scoped to `REG1_MS1.pdf` only** — applying either fix corpus-wide mislabeled a genuine REG2A10 chunk during this fix's own dry-run (Annexe III content relabeled Annexe II), so left every other file's pre-existing behavior untouched. Also found and **reverted** an article-side companion bug (ToC lines outranking real headers) after verifying its fix made things worse (consolidated several distinct wrong labels into one with a wider blast radius) — documented as a separate, deliberately open issue. Residual: the fixed chunks now show `N.7.2` (the untouched article-side bug), not `Annexe X` — real improvement, not a full fix; guard tests and the new `check.py` invariant (`10. Annexe section plausibility`, WARN if a file's annexe-titled chunks are a minority of its total) say so explicitly in their docstrings.

**Part 2 — expansion default**: `AskRequest.expand_query` now defaults to `True` (opt-out). Charline's entire 07-13 retest ran with it `false` on all 9 questions. Couldn't inspect the Figma demo's actual payload (no demo code in this repo) — flagged that if the demo hardcodes `expand_query: false` explicitly, this server-side default alone won't activate it. `expansion_ms` was already captured separately in the session log's `latency_ms` (no change needed) — confirmed live in the smoke test below.

**Part 3 — shipped**: re-ingested `reglement_ecrit` (`--rebuild --family`), `aria-rag check --strict` → **0 FAIL** (invariant #10 flipped WARN→PASS, matching the audit's 45-chunk count exactly). `eval --no-llm`: **80.0%** (no expansion) — byte-identical per-case scores to pre-fix runs (07-11, 07-12), zero movement. With expansion: **86.7%** vs the documented **91.7%** reference — the entire 5-point gap is **one case, CH-03**, and only CH-03: its golden-dataset `expected_articles` contains the literal string `"Annexe X"` (not a real article code, unlike every other entry), evidently calibrated against the mislabeled chunk sometimes surfacing in top-10 by coincidence; now correctly relabeled, it can't. Separately (and independent of this fix): the actual 91.7% reference runs (all 2026-07-10) retrieved from `REG1.pdf`/`REG2A1.pdf` — files this corpus now excludes as **superseded** — so that reference was already stale relative to today's corpus before this fix touched anything, and CH-03 was *already* flaky same-day in 2026-07-10 (swung 91.7%/86.7% across runs, failing on `UG.2.2.3` back then, not `Annexe X`). **Recommend flagging CH-03's `expected_articles` for Charline's review** rather than accepting 91.7% as re-certified — today's 80.0%/86.7% pairing is the current, self-consistent, honestly-measured pair.

**Verified live**: killed the stale pre-fix server, restarted, smoke-tested `/ask` with no `expand_query` field sent — `expand_query_requested: true`, `expansion_status: ok`, `expansion_ms: 21243.8` visible as its own `latency_ms` field, a citation shows `section: "N.7.2"` (the documented residual, not `Annexe X`). Full suite 170 passed throughout.

**Open threads**:
- The article-side ToC bug (reverted fix) is real and undiagnosed-to-a-safe-fix — needs its own audit before any attempt, per this session's own cautionary tale.
- CH-03's `expected_articles: ["UG.2.2.3", "Annexe X"]` needs Charline/Anna's call: fix the golden case, or accept the new 86.7% as certified.
- The 91.7% reference's staleness (measured against now-superseded `REG1.pdf`/`REG2A1.pdf`) is a pre-existing gap unrelated to this fix — worth a full re-certification pass independent of today's work.
- Whether the Figma demo hardcodes `expand_query: false` is unverified — if it does, the server-side default change alone won't fix Charline's next session.

---

## 2026-07-14 — synthesis latency audit (read-only, 0 commits)

**Done**: decomposed Charline's 52–105s synthesis latency into prompt-processing vs generation using (1) her 9 session-log answer lengths, (2) a controlled 3-question reprobe (Q5/Q7/Q9) hitting Ollama directly to capture `prompt_eval_count/duration` + `eval_count/duration` (the app discards these — a real logging gap), (3) git-archaeology on `eval/results/`'s fidelity JSONs, which **carry zero timing data at all** — the "~40s bench" figure only exists in commit-message prose (`8c5e22f`), not a structured log.

**Verdict**: prompt-processing is a near-fixed **~40s floor** (10 chunks × ~3700 prompt tokens @ ~93 tok/s) — only ~2s of that is attributable to the grounding/marker system-prompt growth (479→1326 chars); the rest predates it and was already in the "~40s bench" baseline. Generation time = tokens-generated (avg ~800/question, 300–1083 range, driven by grounding+markers making answers longer, enabled by `num_predict` 1024→1280 not forcing them shorter) ÷ effective tok/s. **Unplanned finding**: my reprobe (today, after this session's own sustained GPU use across two prior audits) measured generation at a flat **16.1 tok/s** — but backing out the same math from Charline's *original* session numbers implies **~20–47 tok/s (median ~25–28)**, roughly double. Same hardware, same resident model — strongly points to thermal/GPU-state variance (suspect d), not a code regression, and means my own probe was itself measured under degraded conditions.

**Reality check**: even at best-case ~28 tok/s (no thermal degradation), a typical ~800-token grounded answer costs ~40s (prompt) + ~29s (gen) ≈ 69s — still nowhere near <40s. On this hardware (GTX 1070), <40s for a full grounded+marked answer isn't reachable without either shortening answers (a prompt-level concision instruction, trades against the grounding contract's thoroughness) or shrinking the prompt (fewer/shorter retrieved chunks, a retrieval-quality tradeoff) or better/cloud GPU. Levers listed, none applied (read-only per instruction).

**Also found, not asked (verified before writing this, initial version was wrong)**: `run_fidelity_grid.py` (the synthesis-fidelity/grounding-audit grid behind `b41d076`/`5e65177`'s measurements) defaults to `top_k=8`, but production (`/ask`, `aria-rag eval`) defaults to `top_k=10` — both default to 10 in `cli.py`'s `eval` subcommand, so the **certified 80%/91.7% retrieval reference is unaffected** (it's measured at top_k=10, matching production). The top_k=8 vs 10 gap is narrower than first thought: only the grounding-quality fidelity grid used a different chunk count than what Charline actually saw — worth noting, not the finding I almost overstated it as.

**Open threads**:
- The thermal/GPU-state hypothesis (median tok/s ~28 live vs ~16 today) is inferred from before/after arithmetic, not a direct temperature/clock readout — worth a real `nvidia-smi`-based measurement if this becomes a work item.
- `log_ask_call` doesn't capture Ollama's `prompt_eval_count`/`eval_count`/durations — the only way to get a real prompt/generation split today is an out-of-band reprobe like this one. Worth adding to the session-log schema (parallel to the `git_commit` gap already noted).
- The `top_k=10` (production) vs `top_k=8` (certified eval reference) mismatch is unexplored — flagging for a separate audit, not chased here.

---

## 2026-07-14 — synthesis determinism audit + seed parity fix (1 commit)

**Done**: audited `answer_with_ollama` (`llm.py:101`) against the R0 determinism fix in `_expand_with_ollama` (`query_expansion.py:97`, which added `seed: 42` alongside `temperature: 0` after a documented CPU-nondeterminism issue). Confirmed `/ask`, `aria-rag ask` CLI, and `eval.py`'s fidelity-grid runner all share one call site (`answer_question` → `answer_with_ollama`) — no divergent path. **Payload diff found**: synthesis's `options` had `temperature: 0` (correctly nested) but **no `seed`**, unlike expansion's `{"temperature": 0, "seed": 42}`.

**Reproduction (pre-fix)**: ran Q7's exact question ("quelles sont les règles de gabarit enveloppe ?") 3× sequentially via `aria-rag ask --backend ollama`, model resident for runs 2–3. **Byte-identical across all 3 runs**, matching Q7's original answer exactly — did **not** reproduce the Q7→Q8 divergence even with `seed` absent. Diagnosis didn't cleanly fit "missing param caused the observed bug" (case 1) since the bug didn't reproduce either way.

**Decision (Anna's call)**: add `seed: 42` anyway — zero-cost, closes the parity gap with the R0 precedent, removes one variable permanently — **but log the live Q7/Q8 divergence as an open, unconfirmed anomaly, not a solved bug**. Added `"seed": 42` to `answer_with_ollama`'s options (`llm.py`). New guard test `test_answer_with_ollama_sends_seed_alongside_temperature` (`test_llm.py`) asserts the payload shape going forward. Re-ran the same 3-generation reproduction post-fix: still byte-identical. Full suite **159 passed** (was 158). `eval.py` untouched, `eval --no-llm` not exercised (no need, not touched).

**Decision rule for the unconfirmed anomaly**: if a same-question divergence recurs in a future Charline session **with `seed` now set**, that's the signal to open the concurrent-GPU-load/platform-nondeterminism investigation (retrieval's embedding model running interleaved with synthesis under real demo load is the leading hypothesis, untested). Until then, it stays a noted anomaly, not a work item — do not proactively chase it.

**Open threads**:
- All existing fidelity measurements (`eval/results/synthesis_fidelity_*.json`) predate this fix and sampled the seed-less synthesis config. Per instruction, **not re-run now** — flag before trusting them as reproducing exactly, though the practical effect of adding seed with no observed content change in the reproduction suggests low risk of them being wrong, just unconfirmed under the new config.
- The concurrent-GPU-load hypothesis for the original Q7/Q8 divergence remains untested — see decision rule above for when to revisit.

---

## 2026-07-14 — Charline retest debrief (read-only, 0 commits)

**Done**: audited Charline's actual 2026-07-13 retest (18:36–20:01 UTC, 9 `/ask` calls, `data/sessions/session_2026-07-12_480ffc42.jsonl` — the process I started for the 07-12 serve-hardening verification and never restarted) against `feedback.jsonl`. Full per-question table + classification delivered in chat, not reproduced here. Counts: **4 [R]** (retrieval), **3 [S]** (synthesis organization), **2 [OK]**, **0 [C]**, **0 [C-cch]**.

**Top findings**: (1) `expand_query_requested: false` on **all 9 questions** — the Figma demo never engaged query expansion, so she tested the 80%-retrieval config, not the measured 91.7% one. (2) Synthesis latency **52–105s per question even when the model was resident** (well above CLAUDE.md's ~20s swap-cost baseline) — likely a bigger driver of "not conclusive" than any single content bug. (3) **New bug**: `REG1_MS1.pdf`'s Définitions glossary (page 34, contains the real "prospect" definition) is mislabeled with `section: "Annexe X – Liste des immeubles protégés..."` — a new instance of the magnet-chunk class, distinct from the fixed lowercase-`annexe` case in `check.py`, and a plausible direct cause of her "difficile de se repérer dans les citations" feedback. (4) **Apparent non-determinism**: Q7/Q8 (identical question + identical hits + resident model, 81s apart) produced different synthesis output — contradicts the documented temperature=0 guarantee.

**Sanity gap confirmed real**: session log entries carry no `git_commit` — I could only *infer* (via no server restart) that all 9 ran on `69a7920`-era code, not prove it per-entry. Worth adding `git_commit` to `log_ask_call`'s entry schema.

**Golden-case drafts (not in eval/, pending Charline review)**: Q1/Q4 fold into existing CH-07 (same articles, same [R], third independent confirmation incl. the 07-11 feedback placeholder). New CH-08 draft from Q6–Q9 (gabarit-enveloppe, a synthesis-organization case, not retrieval). Q3 ("façade N") not draftable yet — genuinely ambiguous zone-code-vs-orientation, needs her clarification first.

**Open threads**:
- The section-mislabeling bug (finding #3) needs its actual scope characterized — how many chunks/how much of the Définitions section is affected — before anyone attempts a fix.
- The Q7/Q8 non-determinism needs reproduction outside a live demo session to rule out an environmental cause (concurrent request, GPU scheduling) before treating it as a code bug.
- Next step: this debrief goes to Charline/Anna for review; CH-08 draft and the "turn on expansion by default" recommendation are the two highest-leverage next actions.

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
