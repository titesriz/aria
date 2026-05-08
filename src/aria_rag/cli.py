from __future__ import annotations

import argparse
import os
import textwrap
from pathlib import Path

from aria_rag.config import load_settings
from aria_rag.indexer import build_index
from aria_rag.llm import answer_question
from aria_rag.retriever import SearchHit, search, search_weighted


def default_worker_count() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(4, cpu_count // 2 or 1))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ARIA RAG starter CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Extract PDFs and build the local index")
    ingest_parser.add_argument("--max-files", type=int, default=None, help="Limit PDFs for quick tests")
    ingest_parser.add_argument(
        "--workers",
        type=int,
        default=default_worker_count(),
        help="Parallel PDF extraction workers. Defaults to a conservative laptop-safe value.",
    )
    ingest_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Ignore the incremental cache and rebuild the whole index",
    )
    ingest_parser.add_argument(
        "--family",
        nargs="+",
        metavar="FAMILY",
        default=None,
        help=(
            "Restrict rebuild to one or more document families (requires --rebuild). "
            "Choices: reglement_ecrit, reglement_graphique, rapport_presentation, oap, padd, annexes, other"
        ),
    )

    eval_parser = subparsers.add_parser("eval", help="Run evaluation against the golden dataset")
    eval_parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to golden_dataset.json (default: eval/golden_dataset.json)",
    )
    eval_parser.add_argument("--top-k", type=int, default=8, help="Number of chunks retrieved per query")
    eval_parser.add_argument(
        "--backend",
        choices=["openai", "ollama", "claude"],
        default="ollama",
        help="LLM backend for answer synthesis (default: ollama)",
    )
    eval_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory for result JSON files (default: eval/results/)",
    )
    eval_parser.add_argument(
        "--ids",
        nargs="+",
        metavar="UC-ID",
        help="Restrict evaluation to specific use case IDs (e.g. UC-01 UC-03)",
    )
    eval_parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout in seconds per query (default: 120)",
    )
    eval_parser.add_argument(
        "--expand-query",
        action="store_true",
        default=False,
        help="Run with query expansion and print a before/after retrieval comparison.",
    )
    eval_parser.add_argument(
        "--alpha",
        type=float,
        default=0.7,
        metavar="FLOAT",
        help="Weight for original query in weighted retrieval (default 0.7). Only used with --expand-query.",
    )
    eval_parser.add_argument(
        "--multi-alpha",
        action="store_true",
        default=False,
        help="Run 3 evals: baseline, alpha=0.7, alpha=0.5 and display a comparison table. Implies --expand-query.",
    )

    ask_parser = subparsers.add_parser("ask", help="Search the index and optionally synthesize an answer")
    ask_parser.add_argument("question", help="Question to ask")
    ask_parser.add_argument("--top-k", type=int, default=None, help="Number of retrieved chunks")
    ask_parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Only return retrieved passages, even if OPENAI_API_KEY is set",
    )
    ask_parser.add_argument(
        "--backend",
        choices=["openai", "ollama", "claude"],
        default=None,
        help="LLM backend for answer synthesis",
    )
    ask_parser.add_argument(
        "--family",
        nargs="+",
        metavar="FAMILY",
        default=None,
        help=(
            "Restrict search to one or more document families. "
            "Choices: reglement_ecrit, reglement_graphique, rapport_presentation, oap, padd, annexes, other"
        ),
    )
    ask_parser.add_argument(
        "--expand-query",
        action="store_true",
        default=False,
        help="Expand the query with inferred PLU article codes before retrieval (requires Ollama).",
    )
    ask_parser.add_argument(
        "--alpha",
        type=float,
        default=0.7,
        metavar="FLOAT",
        help="Weight for original query vs expansion (0.0–1.0, default 0.7). Only used with --expand-query.",
    )
    return parser


def format_hits(hits: list[SearchHit]) -> str:
    blocks: list[str] = []
    for hit in hits:
        excerpt = textwrap.shorten(hit.content, width=500, placeholder="...")
        blocks.append(f"[score={hit.score:.3f}] [{hit.doc_family}] {hit.source_path}\n{excerpt}")
    return "\n\n".join(blocks)


def report_ingest_progress(
    index: int, total: int, path: Path, chunk_count: int, status: str
) -> None:
    print(f"[{index}/{total}] {status.upper():9s} {path.name} -> {chunk_count} chunks", flush=True)


def report_ingest_heartbeat(completed: int, total: int, pending: int) -> None:
    print(
        f"[{completed}/{total}] WORKING   still extracting {pending} file(s)...",
        flush=True,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()

    if args.command == "ingest":
        if args.max_files is not None:
            settings.max_files = args.max_files
        if args.family and not args.rebuild:
            raise SystemExit("--family requires --rebuild")
        print(
            f"Starting ingestion from {settings.docs_dir} with {args.workers} worker(s)...",
            flush=True,
        )
        if args.rebuild and args.family:
            print(f"Partial rebuild: reprocessing family {args.family} only.", flush=True)
        elif not args.rebuild:
            print("Incremental mode is on: unchanged PDFs will be reused from the existing index.", flush=True)
        file_count, chunk_count = build_index(
            settings,
            workers=args.workers,
            progress_callback=report_ingest_progress,
            heartbeat_callback=report_ingest_heartbeat,
            rebuild=args.rebuild,
            family_filter=args.family,
        )
        print(f"Indexed {file_count} PDF files into {chunk_count} chunks at {settings.index_dir}")
        return

    if args.command == "eval":
        from aria_rag.eval import run_eval, _print_multi_comparison
        if args.multi_alpha:
            print("Run 1/3 — baseline sans query expansion\n")
            r_baseline = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=False,
            )
            print("\nRun 2/3 — avec query expansion alpha=0.7\n")
            r_07 = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=True, alpha=0.7,
            )
            print("\nRun 3/3 — avec query expansion alpha=0.5\n")
            r_05 = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=True, alpha=0.5,
            )
            _print_multi_comparison([
                ("Baseline", r_baseline),
                ("α=0.7", r_07),
                ("α=0.5", r_05),
            ])
        elif args.expand_query:
            print("Étape 1/2 — baseline sans query expansion\n")
            r_baseline = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=False,
            )
            print(f"\nÉtape 2/2 — avec query expansion alpha={args.alpha}\n")
            r_expanded = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=True, alpha=args.alpha,
            )
            _print_multi_comparison([
                ("Baseline", r_baseline),
                (f"α={args.alpha}", r_expanded),
            ])
        else:
            run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
            )
        return

    if args.command == "ask":
        query = args.question
        if args.expand_query:
            from aria_rag.query_expansion import expand_query
            backend_for_expansion = args.backend or settings.llm_backend
            original_q, expansion_q, inferred_articles = expand_query(
                query,
                backend=backend_for_expansion,
                ollama_host=settings.ollama_host,
                ollama_model=settings.ollama_model,
            )
            if inferred_articles:
                print(f"[query expansion] articles inférés : {inferred_articles}")
                print(f"[query expansion] expansion query  : {expansion_q}\n")
            hits = search_weighted(
                settings,
                query_original=original_q,
                query_expansion=expansion_q,
                top_k=args.top_k,
                alpha=args.alpha,
                family_filter=args.family,
            )
        else:
            hits = search(settings, query, top_k=args.top_k, family_filter=args.family)
        if not hits:
            print("No relevant passages found.")
            return

        print("Retrieved passages:\n")
        print(format_hits(hits))

        if args.no_llm:
            return

        backend = args.backend or settings.llm_backend
        print("\nLLM answer:\n")
        try:
            print(answer_question(args.question, hits, settings, backend))
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
