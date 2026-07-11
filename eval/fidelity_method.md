# Synthesis fidelity scoring — method

Grounding measurement for `SYSTEM_PROMPT` changes in `src/aria_rag/llm.py`. Separate
from the existing Retrieval/Answer-Coverage scores in `eval/eval.py` (which check
*whether the right chunks were fetched* and *whether expected keywords appear*) —
this method checks whether the synthesized answer's *claims* are actually backed by
the chunks that were fetched, regardless of whether those chunks were the right ones.

## Grid

13 cases, run sequentially, same backend/top_k/timeout for every run being compared:

- `eval/golden_dataset.json` — 10 cases (UC-01/02/03/04/05/16, CH-01/03/04/06)
- `eval/adversarial_dataset.json` — 3 cases (ADV-A/B/C), each targeting a distinct
  gap-filling failure mode: a known-weak-retrieval question (ADV-A), an off-corpus
  question that should get a clean refusal (ADV-B), and a plausible-but-nonexistent
  reference that should get an honest "not found" (ADV-C)

Both together via `scripts/run_fidelity_grid.py --label <baseline|fixed>`, which
combines them into one `eval/results/synthesis_fidelity_{date}_{label}.json`.

## Claim-level scoring

For each case, read `raw_answer` against that same run's `raw_passages` (the actual
retrieved context the model saw — not the golden dataset's `expected_*` fields, which
describe the *ideal* retrieval, not what was actually fetched). Walk the answer's
factual assertions — specific numbers, article citations, named exceptions/secteurs,
regulatory terms — and classify each:

- **SUPPORTED** — the claim's specifics (the number, the named secteur, the cited
  article's actual content) appear in `raw_passages`, not just a topically-adjacent
  passage. A citation is not enough; the cited passage must actually say what the
  claim says it says.
- **UNSUPPORTED** — presented as fact but absent from `raw_passages`: an invented
  number, a rule attributed to a secteur/article that doesn't carry it in the
  retrieved text, or a regulatory concept (POS, COS, ZPPAUP) introduced from outside
  the corpus.
- **GENERIC** — an honest gap statement ("le contexte ne précise pas...", "consultez
  X pour confirmer") or a hedge that doesn't smuggle in an unsourced specific. This is
  the desired behavior when retrieval is thin, not a failure.

A case's fidelity is the presence/absence of UNSUPPORTED claims, not a percentage —
one confidently-stated invented number is the failure mode this fix targets,
regardless of how much correctly-sourced material surrounds it.

## What to watch for across a fix

- **Fabrication rate**: count of cases with >=1 UNSUPPORTED claim, before vs after.
- **Over-refusal regression**: cases that were previously complete, well-grounded
  answers (UC-02, UC-04, UC-16, CH-04 in the baseline run) must stay complete — if a
  prompt change makes the model start hedging on material that *is* actually in
  `raw_passages`, GENERIC is being used to avoid answering instead of to flag a real
  gap, and that's a regression, not an improvement.
- **Adversarial behavior matches `expected_behavior`**: ADV-A should end in an honest
  partial-gap statement, not a confident multi-page brief; ADV-B should refuse and
  *stop*, not disclaim once and continue anyway; ADV-C should say "not found," not
  invent a plausible-sounding number.

## Known limitation

`raw_passages` in the results JSON is truncated (each hit capped at ~200 chars by the
`--debug` CLI output `eval.py` parses). A claim that can't be confirmed or denied
against the visible excerpt is noted as such rather than guessed at.
