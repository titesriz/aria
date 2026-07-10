from __future__ import annotations

import argparse
import os
import sys
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
    ingest_parser.add_argument(
        "--strict-check",
        action="store_true",
        default=False,
        help="Exit non-zero if the invariant check that runs automatically after ingest finds any FAIL. For CI.",
    )
    ingest_parser.add_argument(
        "--skip-check",
        action="store_true",
        default=False,
        help="Skip the automatic invariant check after ingest.",
    )

    check_parser = subparsers.add_parser("check", help="Run ingestion invariant checks against the current index")
    check_parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit non-zero if any invariant FAILs. For CI.",
    )

    eval_parser = subparsers.add_parser("eval", help="Run evaluation against the golden dataset")
    eval_parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to golden_dataset.json (default: eval/golden_dataset.json)",
    )
    eval_parser.add_argument("--top-k", type=int, default=10, help="Number of chunks retrieved per query")
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
        default=0.5,
        metavar="FLOAT",
        help="Weight for original query in weighted retrieval (default 0.5). Only used with --expand-query.",
    )
    eval_parser.add_argument(
        "--multi-alpha",
        action="store_true",
        default=False,
        help="Run 3 evals: baseline, alpha=0.7, alpha=0.5 and display a comparison table. Implies --expand-query.",
    )
    eval_parser.add_argument(
        "--no-llm",
        action="store_true",
        default=False,
        help="Skip LLM answer synthesis — score retrieval only. Useful to isolate retrieval from LLM latency.",
    )
    eval_parser.add_argument(
        "--refresh-expansions",
        action="store_true",
        default=False,
        help="Regenerate cached query-expansion results instead of reusing eval/expansion_cache.json.",
    )
    eval_parser.add_argument(
        "--strict-expansion",
        action="store_true",
        default=False,
        help="Exit non-zero if any case's query expansion failed (expansion_status=\"failed\"). For CI.",
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
        default=0.5,
        metavar="FLOAT",
        help="Weight for original query vs expansion (0.0–1.0, default 0.5). Only used with --expand-query.",
    )
    ask_parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Print per-chunk FAISS, BM25 and RRF scores before the retrieved passages.",
    )
    ask_parser.add_argument(
        "--expansion-cache",
        type=Path,
        default=None,
        metavar="PATH",
        help="Cache query-expansion results in this JSON file, keyed by question text — "
             "insurance against residual CPU-backend nondeterminism even at temperature=0.",
    )
    ask_parser.add_argument(
        "--refresh-expansions",
        action="store_true",
        default=False,
        help="Force recomputation of a cached expansion entry instead of reusing it.",
    )
    ask_parser.add_argument(
        "--no-scoped-retrieval",
        action="store_true",
        default=False,
        help="Disable per-family retrieval passes — fall back to one global ranking "
             "across all families (the pre-scoped-retrieval behavior). Ignored when "
             "--family is given (that already restricts to a single pool). For A/B comparison.",
    )

    subparsers.add_parser("serve", help="Start the FastAPI HTTP server on port 8000")

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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()

    if args.command in ("ask", "eval"):
        print(
            f"[models] expansion={settings.expansion_model}  synthesis={settings.synthesis_model}",
            flush=True,
        )

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
        if not args.skip_check:
            from aria_rag.check import run_checks
            print()
            run_checks(settings, strict=args.strict_check)
        return

    if args.command == "check":
        from aria_rag.check import run_checks
        run_checks(settings, strict=args.strict)
        return

    if args.command == "eval":
        from aria_rag.eval import run_eval, _print_multi_comparison
        no_llm = args.no_llm
        refresh_expansions = args.refresh_expansions
        strict_expansion = args.strict_expansion
        if args.multi_alpha:
            print("Run 1/3 — baseline sans query expansion\n")
            r_baseline = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=False, no_llm=no_llm, refresh_expansions=refresh_expansions,
                strict_expansion=strict_expansion,
            )
            print("\nRun 2/3 — avec query expansion alpha=0.7\n")
            r_07 = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=True, alpha=0.7, no_llm=no_llm, refresh_expansions=refresh_expansions,
                strict_expansion=strict_expansion,
            )
            print("\nRun 3/3 — avec query expansion alpha=0.5\n")
            r_05 = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=True, alpha=0.5, no_llm=no_llm, refresh_expansions=refresh_expansions,
                strict_expansion=strict_expansion,
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
                expand_query=False, no_llm=no_llm, refresh_expansions=refresh_expansions,
                strict_expansion=strict_expansion,
            )
            print(f"\nÉtape 2/2 — avec query expansion alpha={args.alpha}\n")
            r_expanded = run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                expand_query=True, alpha=args.alpha, no_llm=no_llm, refresh_expansions=refresh_expansions,
                strict_expansion=strict_expansion,
            )
            _print_multi_comparison([
                ("Baseline", r_baseline),
                (f"α={args.alpha}", r_expanded),
            ])
        else:
            run_eval(
                dataset_path=args.dataset, top_k=args.top_k, backend=args.backend,
                ids=args.ids, results_dir=args.output, timeout=args.timeout,
                no_llm=no_llm, refresh_expansions=refresh_expansions,
                strict_expansion=strict_expansion,
            )
        return

    if args.command == "serve":
        from aria_rag.api import serve
        serve()
        return

    if args.command == "ask":
        query = args.question
        if args.expand_query:
            from aria_rag.query_expansion import expand_query
            backend_for_expansion = args.backend or settings.llm_backend
            original_q, expansion_q, inferred_articles, expansion_status = expand_query(
                query,
                backend=backend_for_expansion,
                ollama_host=settings.ollama_host,
                ollama_model=settings.expansion_model,
                cache_path=args.expansion_cache,
                refresh=args.refresh_expansions,
            )
            print(f"[query expansion] status : {expansion_status}")
            if inferred_articles:
                print(f"[query expansion] articles inférés : {inferred_articles}")
                print(f"[query expansion] expansion query  : {expansion_q}\n")
            elif expansion_status == "failed":
                print("[query expansion] WARNING: expansion failed — falling back to original query\n")
            else:
                print()
            hits = search_weighted(
                settings,
                query_original=original_q,
                query_expansion=expansion_q,
                top_k=args.top_k,
                alpha=args.alpha,
                family_filter=args.family,
                scoped=not args.no_scoped_retrieval,
            )
        else:
            hits = search(
                settings, query, top_k=args.top_k, family_filter=args.family, debug=args.debug,
                scoped=not args.no_scoped_retrieval,
            )
        if not hits:
            print("No relevant passages found.")
            return

        if args.debug:
            from pathlib import Path as _Path
            print("Debug — chunks retrieved:\n")
            for i, hit in enumerate(hits):
                fname = _Path(hit.source_path).name
                faiss_str = f"{hit.faiss_score:.4f}" if hit.faiss_score is not None else "n/a"
                bm25_str  = f"{hit.bm25_score:.4f}"  if hit.bm25_score  is not None else "n/a"
                if hit.page is None:
                    page_str = "n/a"
                elif hit.page_end is None or hit.page_end == hit.page:
                    page_str = str(hit.page)
                else:
                    # Single whitespace-free token — eval.py's debug-output
                    # parser expects exactly one word between "Page:" and
                    # "Section:" (see eval.py's _DEBUG_SECTION_LINE regex).
                    page_str = f"{hit.page}–{hit.page_end}"
                section_str = hit.section if hit.section is not None else "n/a"
                print(f"[{i+1}] {fname} | {hit.doc_family}")
                print(f"     FAISS: {faiss_str}  BM25: {bm25_str}  RRF: {hit.score:.5f}")
                print(f"     Page: {page_str}  Section: {section_str}")
                print(f"     {hit.content[:200]!r}")
                print()

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
