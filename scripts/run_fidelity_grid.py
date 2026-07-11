"""One-off runner: 10 golden + 3 adversarial cases, sequential, combined into
one synthesis_fidelity_{label}.json for before/after grounding-prompt comparison.
Not part of the package — ad hoc script for this measurement, safe to delete after.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aria_rag.eval import DEFAULT_DATASET, run_eval

REPO_ROOT = Path(__file__).parent.parent
ADVERSARIAL_DATASET = REPO_ROOT / "eval" / "adversarial_dataset.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="baseline | fixed")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--backend", default="ollama")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    print(f"=== GOLDEN (10 cases) — label={args.label} ===")
    golden = run_eval(
        dataset_path=DEFAULT_DATASET,
        top_k=args.top_k,
        backend=args.backend,
        results_dir=RESULTS_DIR,
        timeout=args.timeout,
    )

    print(f"\n=== ADVERSARIAL (3 cases) — label={args.label} ===")
    adversarial = run_eval(
        dataset_path=ADVERSARIAL_DATASET,
        top_k=args.top_k,
        backend=args.backend,
        results_dir=RESULTS_DIR,
        timeout=args.timeout,
    )

    combined = golden + adversarial
    date_str = datetime.now().strftime("%Y%m%d")
    out_path = RESULTS_DIR / f"synthesis_fidelity_{date_str}_{args.label}.json"
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCombined 13-case grid -> {out_path}")
