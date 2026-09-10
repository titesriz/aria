# FINDINGS — RP_CHOIX.pdf (control) and Rapport_presentation_MS1.pdf (2-column hard case)

Pilot chain: REG1 → REG2A1 → REG2A10 → these two Rapport de présentation files. Both were first run
**unchanged** through the validated REG2A10 script (`scratch/extract_sommaire_tree_reg2_a10.py`,
only `PDF_PATH`/`OUT_PATH` swapped). Method for both targets: confirm honestly what breaks, then
build a fork (`scratch/extract_sommaire_tree_rp.py`) with generalized fixes — never a per-document
hack — and re-verify non-regression on `RP_CHOIX.pdf` itself and on `REG2A1_MS1.pdf`.

## As-is run: what actually happened (both surprised the stated expectations)

**RP_CHOIX.pdf** (assumed 1-column "control", expected "likely fine") — **was NOT fine**. 120
warnings, most entries corrupted or dropped. Root cause, confirmed by inspecting raw
`get_text("dict")` spans: this document's numbering tokens ("1.", "1.1.", "1.1.1.") sit **alone on
their own physical line**, with the title text on the *next* line — a shape that never occurred in
REG1/REG2/REG2A10 (numbering and title always shared one line there). The old grouping logic has no
concept of "this line is a bare numbering token waiting for its title": a line with no letters and
no regex match is only ever treated as a continuation of the *preceding* entry, so "1." got glued
onto the tail of the already-closed previous entry, corrupting it, while the real numbering for the
next entry was lost entirely.

**Rapport_presentation_MS1.pdf** (the intended hard case) — sommaire not detected **at all**,
correctly triggering the "aucun sommaire détecté" loud-failure path (that safety net worked exactly
as designed — no silent empty pass). Root cause: TOC-page detection assumed a fixed 5-line header
block (`HEADER_BLOCK_LINES=5`, true for REG1/REG2/REG2A10). This document's running header is only
**3 lines** (title, subtitle, "page N/M"), so unconditionally skipping 5 lines ate past "SOMMAIRE"
itself into real TOC content before the marker check ever ran — a hardcoded assumption breaking on
a new document, not a document-specific quirk. Separately, its TOC pages are confirmed genuinely
**2-column, landscape** (block-level bboxes: left column x0≈81/right column x0≈449, page width
842pt) — the case the task actually targeted.

## (a) GENERIC parts that held, unchanged

- Multi-line joining ("only a strong numbering-prefix can split an unclosed group"), leader-dot
  stripping, page-number extraction, stack-based tree build, dense-page `page_fin` clamp,
  last-branch-entry → `doc.page_count`, full-title anchor matching, unnumbered-entry nesting under
  the last *numbered* ancestor — all reused byte-for-byte from the REG2A10 script and all still
  correct once the three breakages below were fixed.

## (b) What broke, generalized fixes (not per-document hacks)

1. **Isolated numbering tokens.** Added a preprocessing pass, `merge_isolated_numbering_lines`,
   that merges a line matching "bare digit-dot token, trailing dot mandatory" (`^\d+(?:\.\d+)*\.\s*$`)
   with the line immediately following it, before grouping. The **mandatory trailing dot** turned
   out to be load-bearing, not cosmetic: an earlier version without it treated a lone page number
   like `"26"` as an isolated numbering token too (ambiguous — a real top-level entry like `"2."`
   and a stray page number `"26"` look identical without the dot), which glued a page number onto
   the *next* entry's numbering token and produced a bogus level-1 node (`numero="26"`,
   `titre="1.8.3. Penser la nature..."`). Requiring the dot (true for every real numbering token
   observed, never true for a page number) fixed it at the source.
2. **Header-length discovery**, replacing the fixed `HEADER_BLOCK_LINES=5` constant: search the
   first ~10 lines of a candidate page for a TOC-title marker; when several lines match (REG1's own
   header literally reprints "SOMMAIRE" as the running section name *and* as the real title),
   prefer the occurrence whose next line looks like real TOC content. Verified this reproduces
   REG1's original `header_len=5` exactly, and correctly discovers `header_len=4` for RP_CHOIX and
   `header_len=2` for Rapport_presentation_MS1 — three different values from one discovery rule,
   not three hardcoded constants.
3. **New `DIGIT_DOT` numbering type** (`"1"`, `"1.1"`, `"2.14"`, level = count of dot-separated
   groups), additive to REG2A1's `Annexe`/`partie` vocabulary. Here too the **trailing dot had to be
   mandatory**: without it, a wrapped street-address house number like `"14 rue René Villermé..."`
   matched the same shape as a real numbered entry and was misread as a bogus top-level entry
   `numero="14"` — confirmed and fixed the same way as finding #1, on the same principle (a real
   numbering token always carries the dot; incidental digits in body text never do).

## (c) What the 2-column case specifically needed

- **Column decision at BLOCK level, applied to LINE-level content.** The first attempt clustered
  *lines* by x0 directly and broke twice, in opposite directions: (i) on RP_CHOIX, decorative
  header/footer text drawn on the page's right side (a title-on-the-right running header, a
  right-aligned "page N/M ... date" footer) created a spurious second x0 cluster with only ~4 stray
  lines — fixed by requiring both sides to carry a *substantial, comparable* share of content, not
  just "several lines" (≥20% each, floor 10/5). (ii) On Rapport_presentation_MS1's real 2-column
  pages, *line*-level x0 turned out to be far noisier than expected (dot-leader runs and
  hanging-indent wraps scattered line x0 across nearly the whole page width, 71 to 725, with no
  single clean gap) even though *block*-level x0 collapsed cleanly to two dominant values (81: 20
  blocks, 449: 25 blocks). Fixed by deciding the 1-vs-2-column split from block bboxes (a much
  cleaner signal) and only then bucketing the finer-grained lines by that split point for actual
  reading order.
- **A margin band, excluded from column clustering and reading-order competition.** Even with the
  block-level fix, the page's own title/header ("SOMMAIRE" itself, x0=380/y0=73) sat at an
  x-position that didn't align with either real column and got siphoned into the "right" column
  bucket, pushing it deep into the middle of the reordered sequence — invisible to header-length
  discovery, which only scans the first ~10 lines. Fixed with an absolute (not page-height-ratio)
  90pt top/bottom margin band, kept out of column clustering entirely: the header band is prepended
  in its own natural top-to-bottom order (still needed transiently, for marker discovery, before
  being stripped from real content); the **footer band is dropped outright**, never repositioned —
  repositioning it was tried first and caused a real bug (a footer line, "Modification simplifiée
  n°1 ... page 3/103", parsed as a bogus entry with `page_debut=103`, the document's own total page
  count misread as a real TOC page number).
- **Per-page column detection, not per-document.** `Rapport_presentation_MS1.pdf`'s two TOC pages
  are genuinely different shapes: page 3 (physical index 2) is 2-column (49 blocks, split ~23/26),
  page 4 (physical index 3) is single-column (only 19 blocks total, one stray outlier) — confirmed
  by direct block-count inspection, not assumed. The column decision is made independently per
  page, which is why the summary correctly reports `[2, 1]` rather than forcing one verdict across
  the whole TOC range.

## Residual, disclosed imperfections (not silently smoothed over)

Rapport_presentation_MS1.pdf remains the genuinely hardest document in this whole study and is
**not** 100% clean: 26 entries extracted (levels 1-3), **90% anchor match** (9/10, one mismatch —
`2.3`, whose reconstructed title merges text from two adjacent wrapped entries), and a handful of
"no trailing page number, skipped" / "numero=None, kept but flagged" warnings where dense multi-line
wrapping interacts with the column split in ways not fully untangled (e.g. `"Rennequin Suppression
de l'emplacement réservé..."`, a title that mixes fragments from two logically separate entries).
These are flagged in the output's own `warnings[]`, not hidden — the underlying content (an annex
listing dozens of short, similarly-worded "modification" entries, each 1-2 pages, heavily wrapped)
is a harder case than any prior document in this study, and further tightening was judged
diminishing-returns for this pass rather than chased to zero.

## Non-regression (re-run with this SAME hardened script, not the old one)

- `RP_CHOIX.pdf` (the actual control, once its real numbering-token issue was fixed): **110
  entries, 100% anchor match (10/10)**, columns correctly detected as `[1, 1, 1]` on all 3 TOC
  pages.
- `REG2A1_MS1.pdf` (prior validated file): **12 entries, 100% anchor match (10/10)**, identical to
  its originally validated result — the header-discovery and column generalization introduced zero
  regression here.

## Bottom line

Confirms the pattern from every prior extension in this study: the *shape-independent* mechanics
(joining, tree-building, anchor verification) keep generalizing; the *document-specific* triggers
(numbering vocabulary, header length, column layout) correctly do **not** generalize on their own
and need explicit, narrow, evidence-driven rules — three more were added here (isolated-numbering
merge with mandatory dot, discovered header length, block-level column detection with a dropped
footer band), all written to survive being pointed at a *different* document, not hardcoded to
these two files.
