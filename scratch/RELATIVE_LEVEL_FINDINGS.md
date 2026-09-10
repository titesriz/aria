# FINDINGS — relative-level rework (transverse change, all 6 documents)

Script: `scratch/extract_sommaire_tree_relative.py` + shared engine
`scratch/relative_level_engine.py`. Reuses each family's already-validated line-extraction
(REG1's header-based detection, REG2/REG2A10's title-search, RP's column/margin-band pipeline)
byte-for-byte — only the LEVEL computation (and everything mechanically downstream of it: tree
nesting, `page_fin`, anchor check) changed. Nothing in `src/` touched, no PDF modified, nothing
sent to Notion.

## Non-regression — the critical invariant, checked first

For all 4 previously-validated documents, **extraction itself is byte-for-byte unchanged**: same
entry count, same `(numero, titre, page_debut)` sequence in the same order. Only `niveau` values
were free to change, exactly as this task's own framing says they should:

| Document | Entries | Extraction | Levels before | Levels after |
|---|---|---|---|---|
| REG1_MS1 | 161 | unchanged | `{1:4, 2:15, 3:52, 4:88, 5:2}` | `{1:4, 2:19, 3:48, 4:88, 5:2}` |
| REG2A1_MS1 | 12 | unchanged | `{1:9, 2:2, 3:1}` | `{1:9, 2:3}` |
| REG2A10_1DE2_MS1 | 10 | unchanged | `{1:10}` | `{1:10}` |
| REG2A10_2DE2_MS1 | 10 | unchanged | `{1:10}` | `{1:10}` |

**REG2A10 invariant explicitly confirmed**: all 10 arrondissement entries in both volumes stay
flat level 1, exactly as required.

**REG1's "58 kept sections" claim — flagged, does not match observed data.** The currently saved
`reg1_sommaire_tree.json` has 161 entries, not 58, both before and after this rework (extraction
untouched). Levels do still span 1-4 (in fact 1-5, matching the original). I did not force a match
to 58 — reporting the actual number rather than guessing what "kept" might have meant in a prior,
unlogged context. Worth clarifying with you if 58 refers to something else (a manual curation pass,
a different subset, etc.).

**REG1's one real shift, explained, not a bug**: 4 entries moved from level 3 to level 2 — exactly
the 4 zone headers' "Caractère de la zone ..." lines (one each for UG, UGSU, UV, N). Under the old
absolute-depth rule they nested one level below their ALL-CAPS zone header (level 3). Under the new
rule they have no numbering and no ALL-CAPS signal ("no descent indicator") → they now *inherit*
their zone header's own level (2) directly, per rule 2. Intended consequence of the rework, not an
extraction change.

**REG2A1's one shift, same story**: the one `numero=None` stray paragraph (already flagged as an
oddity in the REG2A1 study) moved from level 3 to level 2 — it used to nest one level below
whatever was open ("2e partie", level 2); now it inherits "2e partie"'s own level (2) directly, per
the same rule 2.

## RP_CHOIX.pdf — Axe 1/2/3 check (the task's explicit verification target)

| numero | titre | niveau |
|---|---|---|
| `1` | Les choix retenus pour établir le PADD | **2** |
| `Axe 1` | Une ville en transition vertueuse et résiliente | **3** |
| `Axe 2` | Une ville inclusive, productive et solidaire | **3** |
| `Axe 3` | Une ville qui considère et valorise ses identités urbaines | **3** |

Matches the task's required outcome exactly: `Axe 1/2/3` land at the **same** level (3), one level
deeper than `"1."` (2) — via the FLAT-profile recurring-jump rule (Axe 1's first occurrence
establishes the anchor at level 3; Axe 2 and Axe 3, recognized as the same recurring "AXE" profile,
jump straight back to that anchor regardless of how deep the intervening `1.1/1.1.1/1.2/...`
digit-dot chain had wandered in between). Before this rework, `Axe 2`/`Axe 3` were buried at
inconsistent depths (the old "unnumbered → nest one level below whatever's on the stack" rule had
no concept of "this heading type recurs," so each fresh `Axe N` just kept nesting deeper under
whatever numbered entry happened to precede it).

## Entries per level — both RP documents (relative rule)

**RP_CHOIX.pdf**: `{1: 1, 2: 4, 3: 10, 4: 47, 5: 25, 6: 23}` — 110 entries, **100% anchor match**
(10/10).

**Rapport_presentation_MS1.pdf**: `{1: 4, 2: 21, 3: 1}` — 26 entries, **90% anchor match** (9/10,
same one residual mismatch already disclosed in the prior column-reading study — unrelated to this
level rework).

## A notable, disclosed structural side-effect on RP_CHOIX (not asked for, worth flagging)

Because `"Axe 1"` now computes to level 3 (deeper than `"1."` at level 2) and the intervening
unnumbered line `"La démarche de construction du PADD"` *inherited* level 2 (same as `"1."`),
`"Axe 1"` ends up nested as a **child of `"La démarche de construction du PADD"`** in the tree,
rather than as a sibling of it under `"1."` directly. This is a faithful, correct application of
the specified line-by-line relative rule (position, not meaning) — but it produces a parent/child
relationship a human reading the document wouldn't draw ("La démarche..." is a short intro
paragraph, not a container for the three Axes). Flagging this explicitly rather than silently
presenting the deeper nesting as obviously "more correct" than before — it is a different kind of
correct, by design, and the domain expert should see it before this feeds anything downstream.

## Bottom line

The rework holds up under non-regression: zero change to what gets extracted, only to how depth is
assigned, and every level shift observed is explained by the rule as specified (not a bug). The
task's own explicit target (Axe 1/2/3 co-level) is met. One open question (REG1's "58" figure) and
one side-effect worth a second look (Axe 1's new parent) are flagged for your review rather than
resolved unilaterally.
