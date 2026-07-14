"""Regression test for POST /ask's expand_query default (src/aria_rag/api.py).

2026-07-14: Charline's entire retest (9/9 questions) ran with
expand_query_requested=false, meaning she tested the 80%-retrieval config
rather than the measured 91.7%-with-expansion one (SESSION_STATE.md). Flipped
to opt-out so a client that omits the field gets expansion by default.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.api import AskRequest


def test_expand_query_defaults_to_true_when_omitted():
    req = AskRequest(question="quelles sont les règles de gabarit enveloppe ?")
    assert req.expand_query is True


def test_expand_query_still_honors_explicit_false():
    req = AskRequest(question="quelles sont les règles de gabarit enveloppe ?", expand_query=False)
    assert req.expand_query is False
