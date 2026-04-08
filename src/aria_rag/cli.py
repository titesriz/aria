from __future__ import annotations

import argparse
import os
import textwrap
from pathlib import Path

from aria_rag.config import load_settings
from aria_rag.indexer import build_index
from aria_rag.llm import answer_question
from aria_rag.retriever import SearchHit, search


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
        choices=["openai", "ollama"],
        default=None,
        help="LLM backend for answer synthesis",
    )
    return parser


def format_hits(hits: list[SearchHit]) -> str:
    blocks: list[str] = []
    for hit in hits:
        excerpt = textwrap.shorten(hit.content, width=500, placeholder="...")
        blocks.append(f"[score={hit.score:.3f}] {hit.source_path}\n{excerpt}")
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
        print(
            f"Starting ingestion from {settings.docs_dir} with {args.workers} worker(s)...",
            flush=True,
        )
        if not args.rebuild:
            print("Incremental mode is on: unchanged PDFs will be reused from the existing index.", flush=True)
        file_count, chunk_count = build_index(
            settings,
            workers=args.workers,
            progress_callback=report_ingest_progress,
            heartbeat_callback=report_ingest_heartbeat,
            rebuild=args.rebuild,
        )
        print(f"Indexed {file_count} PDF files into {chunk_count} chunks at {settings.index_dir}")
        return

    if args.command == "ask":
        hits = search(settings, args.question, top_k=args.top_k)
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
