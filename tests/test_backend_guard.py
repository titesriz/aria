"""Guard tests: no production entry point (api /ask, cli ask/eval, serve) can
resolve to any backend other than "ollama" — sovereignty rule, enforced at
three independent layers after the 2026-09-22 OpenAI removal / Claude
production-path removal:
  1. llm.answer_question's dispatch (the actual call-out point).
  2. cli.py's argparse --backend `choices` (ask, eval — serve has no
     --backend flag at all, it only reads Settings.llm_backend).
  3. api.py's per-request synthesis_model lookup dict.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag import llm
from aria_rag.cli import build_parser
from aria_rag.config import Settings
from aria_rag.llm import answer_question
from aria_rag.retriever import SearchHit


def _hit() -> SearchHit:
    return SearchHit(
        source_path="/docs/REG1.pdf", doc_family="reglement_ecrit", score=0.5,
        content="content", page=1, page_end=1, section="UG.1.1",
        faiss_score=0.04, bm25_score=12.3,
    )


# ---------------------------------------------------------------------------
# Layer 1: llm.answer_question dispatch
# ---------------------------------------------------------------------------

def test_answer_question_ollama_dispatches():
    settings = Settings(llm_backend="ollama", synthesis_model="ministral-3:8b")
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": "answer"}
    with patch.object(llm.httpx, "post", return_value=response):
        assert answer_question("q?", [_hit()], settings, "ollama") == "answer"


def test_answer_question_rejects_openai():
    with pytest.raises(RuntimeError, match="Unsupported LLM backend: openai"):
        answer_question("q?", [_hit()], Settings(), "openai")


def test_answer_question_rejects_claude():
    """answer_with_claude still exists in llm.py (reserved for the eval-only
    no-corpus baseline) but must not be reachable through this dispatch."""
    with pytest.raises(RuntimeError, match="Unsupported LLM backend: claude"):
        answer_question("q?", [_hit()], Settings(), "claude")


# ---------------------------------------------------------------------------
# Layer 2: cli.py argparse --backend choices
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subcommand,extra_args", [
    ("ask", ["some question"]),
    ("eval", []),
])
@pytest.mark.parametrize("rejected_backend", ["openai", "claude"])
def test_cli_backend_choices_reject_non_ollama(subcommand, extra_args, rejected_backend):
    parser = build_parser()
    argv = [subcommand, *extra_args, "--backend", rejected_backend]
    with pytest.raises(SystemExit):
        with redirect_stderr(io.StringIO()):
            parser.parse_args(argv)


@pytest.mark.parametrize("subcommand,extra_args", [
    ("ask", ["some question"]),
    ("eval", []),
])
def test_cli_backend_choices_accept_ollama(subcommand, extra_args):
    parser = build_parser()
    args = parser.parse_args([subcommand, *extra_args, "--backend", "ollama"])
    assert args.backend == "ollama"


def test_serve_has_no_backend_flag():
    """serve resolves its backend purely from Settings.llm_backend (default
    "ollama" as of this change) -- confirm it never grew a --backend flag
    that could reintroduce a non-ollama path."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        with redirect_stderr(io.StringIO()):
            parser.parse_args(["serve", "--backend", "openai"])


# ---------------------------------------------------------------------------
# Layer 3: api.py's per-request synthesis_model lookup
# ---------------------------------------------------------------------------

def test_api_synthesis_model_lookup_only_knows_ollama():
    import ast
    import inspect

    from aria_rag import api

    source = inspect.getsource(api.ask)
    tree = ast.parse(source)
    dict_literals = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)]
    assert dict_literals, "expected a dict literal in api.ask (the synthesis_model lookup)"
    keys = {
        k.value for d in dict_literals for k in d.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }
    assert keys == {"ollama"}, f"synthesis_model lookup exposes non-ollama keys: {keys - {'ollama'}}"
