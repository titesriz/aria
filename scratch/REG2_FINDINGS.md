# FINDINGS — extending the sommaire-extraction study to REG2A1 (Tome 2, annex/table type)

Pilot: `scratch/extract_sommaire_tree.py` (REG1_MS1, 250p, outline-style, validated: 161 entries,
5 levels, 100% anchor match).

New target: `Ressources/PLU bioclimatique/Règlement/Pièces écrites/Tome 2/REG2A1_MS1.pdf`
(224p, "ANNEXE I : LISTE DES SECTEURS SOUMIS À DES DISPOSITIONS PARTICULIÈRES", the first Tome 2
annex volume — mostly tables, not a numbered outline).

Step 1 was to run the REG1 script **completely unchanged** against this file:
`scratch/extract_sommaire_tree_reg2_asis.py` (a copy of the pilot with only `PDF_PATH`/`OUT_PATH`
swapped, zero logic changes). Result:

```
Detected sommaire page range (0-based physical indices): []
Parsed 0 raw entries from 0 content lines (0 groups).
Total entries extracted: 0
Warnings: 0
```

**Total silent failure** — no exception, no warning, just an empty tree. That's itself worth
flagging: the pilot has no "I found nothing, something is probably wrong" guard.

## (a) GENERIC parts that held, unchanged

Everything *downstream* of TOC-page detection ported over with zero logic changes and worked
correctly on REG2A1:

- **Multi-line (wrapped) entry joining** — the "a line only starts a new entry once the current
  group has already found its trailing page number; otherwise only a strong numbering-prefix can
  force a split" rule. REG2A1 wraps titles across 2 lines just like REG1 (e.g. "Annexe II : Liste
  des périmètres devant faire l'objet d'un projet d'aménagement" → "global .......... 7"), and one
  wrapped line even starts with a capital letter mid-sentence ("...légende." → "Ils sont
  énumérés..."), which is exactly the ALL-CAPS-wrap failure mode fixed during the REG1 pilot — it
  held here too, for the same underlying reason.
- **Leader-dot stripping + trailing-page-number extraction** (`clean_title_and_extract_page`).
- **Stack-based hierarchy build** (level-tracking stack, push/pop by level).
- **`page_fin`/`nb_pages` via "next entry at same-or-shallower level", with the dense-page clamp**
  — didn't actually fire on REG2A1 (no two entries share a start page here), but the code path is
  identical and available.
- **Anchor verification** (spread sample, ±3-page search window, offset voting) — 10/10 matched,
  100%, dominant offset 0, unchanged from the REG1 approach.
- **Generic "unnumbered → nest under whatever is open" fallback** — reused as-is for the one
  unnumbered line found in this doc (see (c) below).

## (b) REG1-SPECIFIC parts that broke, and why

1. **TOC-page detection (total failure).** REG1 detected sommaire pages by matching the page's
   *header* (2nd line) against the literal string `"SOMMAIRE"` — REG1's running header happens to
   reprint "SOMMAIRE" as the section name on all 4 TOC pages. REG2A1's header instead reprints the
   *content* section title ("ANNEXE I : LISTE DES SECTEURS SOUMIS...") even on its TOC page — the
   header never says "SOMMAIRE" or "Table des matières" anywhere. This is why the as-is run found
   zero pages and, cascading from that, zero entries.
2. **Numbering vocabulary (would have matched nothing anyway).** REG1's three regexes (`PARTIE n`,
   single letter/roman + `.`, code + `.digit` groups like `UG.1.4`) don't match this document's
   scheme at all: `Annexe I :`, `Annexe III :`, `1ère partie :`, `2ème partie :`. Even if TOC
   detection had somehow succeeded, every line would have fallen through to "unnumbered."
3. **No zone-style multi-level numeric nesting.** REG1's depth formula was built around
   `code + N dot-groups`; REG2A1 only has 2 numbering levels total (Annexe, then French-ordinal
   "partie"), so that formula is moot here — not broken, just inapplicable to this document's
   (shallower) structure.

## (c) What REG2A1 needed — new, document-specific rules

- **TOC detection generalized to body-content title search + shape-based continuation**, instead
  of header-text matching: scan the first ~15 pages' *body* (skipping the known 5-line header
  block) for a standalone "Table des matières" or "Sommaire" line, then keep including subsequent
  pages only while ≥30% of their body lines still look like TOC lines (leader-dots and/or a
  trailing page number). This is more robust than REG1's header-based rule and would very likely
  have also worked on REG1 (untested here — out of scope for this pass, flagged as a candidate
  for promotion to the shared/generic logic once confirmed on both).
- **Two new numbering regexes**, specific to this doc's vocabulary: `Annexe <roman> :` (level 1)
  and `<ordinal> partie :` (level 2, French ordinals "1ère"/"2ème"). Zero overlap with REG1's
  regex set — these are additive, not replacements.
- **A new heuristic warning** for a genuine content oddity: one line in the TOC region is
  descriptive body text ("Les emplacements réservés pour création ou élargissement de voies*
  sont indiqués... Ils sont énumérés dans la liste ci-après.") that happens to end in a leader-dot
  run + a page number identical to the entry above it. It parses as a structurally valid
  "unnumbered" entry, but it obviously isn't a real TOC heading. Rather than silently accepting it
  as a tree node, added a heuristic (long, unnumbered, starts with a French determiner like
  "Les"/"Ils") that flags it in `warnings` for human review. It's still *included* in the tree
  (nested under "2ème partie") since suppressing it outright would be guessing — the human should
  decide whether to drop it.

## Bottom line

Confirms the intended lesson: the **shape-independent mechanics (joining, tree-building,
page-range inference, anchor verification)** generalize cleanly across a structurally different
document. The **document-specific parts (TOC-page detection trigger, numbering vocabulary)**
correctly do *not* generalize and had to be rewritten per document type — exactly the signal
needed to decide, with the domain expert, which rules become "try these N numbering grammars in
order" shared logic vs. which stay per-family config (similar in spirit to the existing
`corpus_mapping.yaml` per-family classification already in the real pipeline, though this study
does not touch that file or any production code).
