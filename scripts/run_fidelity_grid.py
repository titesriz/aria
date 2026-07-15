"""One-off runner: 10 golden + 3 adversarial + 1 ad hoc regression case,
sequential, combined into one synthesis_fidelity_{label}.json for before/after
grounding-prompt comparison. Not part of the package — ad hoc script for this
measurement, safe to delete after.

The 14th case (CHECK-VOLETS) isn't in eval/golden_dataset.json or
eval/adversarial_dataset.json on purpose: it's a direct reproduction of the
2026-07-15 cite-then-deny contradiction (session_2026-07-15_94b05ad4.jsonl),
kept here rather than promoted into either certified dataset until
Charline/Anna signs off on it as a permanent golden case.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aria_rag.eval import DEFAULT_DATASET, run_eval

REPO_ROOT = Path(__file__).parent.parent
ADVERSARIAL_DATASET = REPO_ROOT / "eval" / "adversarial_dataset.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

CHECK_VOLETS_CASE = [
    {
        "id": "CHECK-VOLETS",
        "question": "est ce que je peux installer des volets sur mon immeuble?",
        "expected_keywords": [],
        "family": None,
        "complexity": "Moyenne",
        "source": (
            "2026-07-15 session_2026-07-15_94b05ad4.jsonl — cite-then-deny "
            "contradiction: answer quoted OAP_CONSTRUCTION's 'volets roulants "
            "à lames orientables' [4] then concluded 'le contexte ne permet "
            "pas de répondre clairement'."
        ),
    }
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="baseline | fixed")
    parser.add_argument("--top-k", type=int, default=10, help="matches production default (cli.py)")
    parser.add_argument("--backend", default="ollama")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--no-expand-query",
        action="store_true",
        help="Disable query expansion (default: on, matching production's expand_query=True default).",
    )
    args = parser.parse_args()
    expand_query = not args.no_expand_query

    print(f"=== GOLDEN (10 cases) — label={args.label} ===")
    golden = run_eval(
        dataset_path=DEFAULT_DATASET,
        top_k=args.top_k,
        backend=args.backend,
        results_dir=RESULTS_DIR,
        timeout=args.timeout,
        expand_query=expand_query,
    )

    print(f"\n=== ADVERSARIAL (3 cases) — label={args.label} ===")
    adversarial = run_eval(
        dataset_path=ADVERSARIAL_DATASET,
        top_k=args.top_k,
        backend=args.backend,
        results_dir=RESULTS_DIR,
        timeout=args.timeout,
        expand_query=expand_query,
    )

    print(f"\n=== CHECK-VOLETS (1 case) — label={args.label} ===")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(CHECK_VOLETS_CASE, tmp, ensure_ascii=False)
        volets_dataset_path = Path(tmp.name)
    try:
        volets = run_eval(
            dataset_path=volets_dataset_path,
            top_k=args.top_k,
            backend=args.backend,
            results_dir=RESULTS_DIR,
            timeout=args.timeout,
            expand_query=expand_query,
        )
    finally:
        volets_dataset_path.unlink(missing_ok=True)

    combined = golden + adversarial + volets
    date_str = datetime.now().strftime("%Y%m%d")
    out_path = RESULTS_DIR / f"synthesis_fidelity_{date_str}_{args.label}.json"
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCombined 14-case grid -> {out_path}")
