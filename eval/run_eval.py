"""Thin wrapper — run directly or via `aria-rag eval`."""
from __future__ import annotations

import argparse
from pathlib import Path

from aria_rag.eval import DEFAULT_DATASET, DEFAULT_RESULTS_DIR, run_eval

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Évaluation ARIA RAG sur le golden dataset")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, metavar="PATH")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--backend", choices=["openai", "ollama", "claude"], default="ollama")
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR, metavar="DIR")
    parser.add_argument("--ids", nargs="+", metavar="UC-ID")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--expand-query", action="store_true", default=False)
    args = parser.parse_args()
    run_eval(
        dataset_path=args.dataset,
        top_k=args.top_k,
        backend=args.backend,
        ids=args.ids,
        results_dir=args.output,
        timeout=args.timeout,
        expand_query=args.expand_query,
    )
