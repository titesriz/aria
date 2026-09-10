# FINDINGS — REG2A10_1DE2_MS1 & REG2A10_2DE2_MS1 (Annexe X, Protections patrimoniales)

Pilot chain: REG1_MS1 (outline) → REG2A1_MS1 (annex/table, `scratch/extract_sommaire_tree_reg2.py`,
validated: 12 entries, 100% anchor match) → these two files (Annexe X, split in two volumes,
arrondissements 1–10 and 11–20).

Both files were run **unchanged** through the REG2A1 extractor first
(`scratch/extract_sommaire_tree_reg2.py`, only `PDF_PATH`/`OUT_PATH` swapped — see the as-is run
log below). Result on REG2A10_1DE2_MS1:

```
Detected TOC page range: [2]     (correct — TOC detection generalizes fine)
Parsed 10 raw entries             (correct)
Entries per level:
  level 2: 1  level 3: 1  level 4: 1  level 5: 1  level 6: 1  level 7: 1
  level 8: 1  level 9: 1  level 10: 1  level 11: 1
Anchor check: 10/10 matched (rate=100%)
Dominant offset detected: -3 (votes: {0: 1, -3: 9})
```

100% anchor match and a plausible-looking summary — but both the tree shape and the offset are
**wrong**. Two real, generalizable bugs, not two document-specific quirks.

## (a) GENERIC parts that held, unchanged

- **TOC-page detection** (body-content title search + shape-based continuation, generalized during
  the REG2A1 pass) — correctly found the single "Table des matières" page in both files (physical
  index 2 in both, despite one file being 700 pages and the other 889).
- **Multi-line joining / leader-dot stripping / page-number extraction** — all 10 entries per file
  parsed cleanly; no wrapped-title edge cases surfaced here (these titles are short, single-line).
- **`page_fin` dense-page clamp** — present in the code path, didn't fire on these two files (no two
  entries share a start page).

## (b) What broke — and why it's a REG2A1 assumption, not a REG2A10 quirk

1. **Runaway unnumbered nesting (level 2→11 instead of 10 flat siblings).** REG2A1's rule for an
   unnumbered entry was "nest as a child of whatever is currently on top of the build stack." That
   held on REG2A1 because unnumbered entries were the *exception* there (fired exactly once, nested
   correctly under "2e partie"). Here, **every single entry is unnumbered** — all 10
   "LISTE DES PROTECTIONS PATRIMONIALES DU Nème ARRONDISSEMENT" headings are unnumbered ALL-CAPS
   lines with no "Annexe"/"partie" prefix at all. With the old rule, nothing on the stack is ever a
   numbered node, so each new unnumbered entry becomes a *child* of the previous unnumbered entry
   instead of its sibling — producing a spurious 10-level-deep linear chain. This is REG2A1's own
   fallback rule failing on a document shape it was never tested against (a doc with **zero**
   numbered entries), not a new failure mode invented for REG2A10.
2. **False anchor-check offset (-3 instead of the real +1).** REG2A1's anchor matcher compared only
   the entry's first 4 normalized words. That worked because REG2A1's titles diverge from each
   other within the first 4 words. Here, **all 10 titles share the same first 4 words**
   ("LISTE DES PROTECTIONS PATRIMONIALES..."), which also happens to be exactly the running page
   header repeated on every page of *every* arrondissement's section — so the 4-word match hits
   the wrong neighbouring page just as readily as the right one, and the "100% match" was scoring
   false positives. The reported offset (-3) was an artifact of the ±3 search window and iteration
   order, not a real page-numbering fact about the document. Manual verification (opening physical
   pages 47–52 directly) confirms the document's real convention is **printed page N = physical
   index N** (offset **+1** relative to REG1/REG2A1's own -1 convention) — a genuine, and
   previously undetected, per-document difference in printed-vs-physical mapping.

## (c) What these files needed — fixes, kept generalizable, not hardcoded to "arrondissement lists"

Built `scratch/extract_sommaire_tree_reg2_a10.py` (a fork, not an edit, of the REG2A1 script):

- **Unnumbered-entry level rule generalized**: an unnumbered entry now nests one level below the
  most recently seen **numbered** entry (tracked separately from the build stack), defaulting to
  level 1 if no numbered entry has appeared yet in the document. Verified this doesn't regress
  REG2A1's own case (traced by hand: "2e partie" is still the last numbered entry when the stray
  paragraph appears, so it still nests at level 3, same as before).
- **Anchor check now matches the full normalized title**, not a fixed word-count prefix — removes
  the false-positive class entirely (confirmed: 9/10 samples now vote for the same, correct, offset
  +1; the 10th, the first arrondissement, naturally votes 0 since its own boundary page happens to
  coincide with the naive guess).
- **Robustness rules applied per this task's spec**: loud "aucun sommaire détecté" warning path
  wired in (didn't fire — both files have a real TOC); last-entry-of-branch `page_fin` now set to
  `doc.page_count` (700 / 889) instead of `null`, still flagged in warnings; every `numero=None`
  node is now explicitly flagged in warnings per the standing convention.
- **Discrepancy worth flagging rather than silently absorbing**: the task's convention states
  `numero=None` nodes are "fused into parent, not standalone." That fits REG2A1's stray paragraph
  (genuinely incidental text). It does **not** fit here — these 10 unnumbered nodes are the
  document's actual primary structure (one real section per arrondissement, each with its own
  ~40–130-page address table). They're flagged per the convention as instructed, but a
  human should decide whether "fused into parent" is the right semantic label for this shape, or
  whether "unnumbered but structurally primary" deserves its own category before this feeds
  Notion.

## Bottom line

Two real bugs were caught, not two file-specific patches: a fallback rule that only assumed
"unnumbered is the exception" and an anchor-match heuristic that only assumed "titles diverge
early." Both are now written as document-shape-independent rules (tracked last-numbered-level;
full-title matching) rather than special-cased to "annexe X, arrondissement lists." The corrected
extractor produces a clean, flat 10-entry tree per file, 100% anchor match, and a correctly
detected +1 offset — a genuine per-document convention difference from REG1/REG2A1, not an error.
