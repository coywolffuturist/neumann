"""Tests for the claude-CLI tiebreak callback.

The callback shells out to ``claude --print`` in production. Tests stub
``subprocess.run`` so no real LLM call happens.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from neumann.router import (
    FALLBACK_SENTINEL,
    PersonaSelector,
    PlannedTask,
    RoutingContext,
    RoutingFallback,
    TaskType,
    make_claude_cli_tiebreak,
)


def _candidates() -> list[dict]:
    return [
        {"id": "engineer", "name": "Engineer", "description": "general implementation"},
        {"id": "qa-engineer", "name": "QA Engineer", "description": "verifies changes"},
        {"id": "backend-engineer", "name": "Backend Engineer", "description": "APIs, schemas"},
    ]


def _stub_run(stdout: str, returncode: int = 0):
    """Returns a function suitable for monkey-patching subprocess.run."""

    def fake(*args, **kwargs):
        result = subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr="")
        return result

    return fake


def test_tiebreak_returns_chosen_persona() -> None:
    cb = make_claude_cli_tiebreak()
    task = PlannedTask(title="Verify smoke test passes")
    ctx = RoutingContext()

    with patch("neumann.router.tiebreaks.shutil.which", return_value="/opt/homebrew/bin/claude"), \
         patch("neumann.router.tiebreaks.subprocess.run",
               side_effect=_stub_run('{"persona_id": "qa-engineer"}')):
        chosen = cb(task, ctx, _candidates())
    assert chosen == "qa-engineer"


def test_tiebreak_strips_markdown_fence() -> None:
    cb = make_claude_cli_tiebreak()
    fenced = '```json\n{"persona_id": "backend-engineer"}\n```'
    with patch("neumann.router.tiebreaks.shutil.which", return_value="/opt/homebrew/bin/claude"), \
         patch("neumann.router.tiebreaks.subprocess.run", side_effect=_stub_run(fenced)):
        chosen = cb(PlannedTask(title="x"), RoutingContext(), _candidates())
    assert chosen == "backend-engineer"


def test_tiebreak_returns_fallback_on_invalid_persona() -> None:
    """If the LLM picks an id that isn't in the candidate list, fall back."""
    cb = make_claude_cli_tiebreak(fallback_persona="engineer")
    with patch("neumann.router.tiebreaks.shutil.which", return_value="/opt/homebrew/bin/claude"), \
         patch("neumann.router.tiebreaks.subprocess.run",
               side_effect=_stub_run('{"persona_id": "nonexistent"}')):
        chosen = cb(PlannedTask(title="x"), RoutingContext(), _candidates())
    assert chosen == "engineer"


def test_tiebreak_returns_fallback_on_unparseable_output() -> None:
    cb = make_claude_cli_tiebreak(fallback_persona="engineer")
    with patch("neumann.router.tiebreaks.shutil.which", return_value="/opt/homebrew/bin/claude"), \
         patch("neumann.router.tiebreaks.subprocess.run",
               side_effect=_stub_run("I don't know, ask someone else.")):
        chosen = cb(PlannedTask(title="x"), RoutingContext(), _candidates())
    assert chosen == "engineer"


def test_tiebreak_returns_fallback_on_subprocess_error() -> None:
    cb = make_claude_cli_tiebreak(fallback_persona="engineer")
    with patch("neumann.router.tiebreaks.shutil.which", return_value="/opt/homebrew/bin/claude"), \
         patch("neumann.router.tiebreaks.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=60)):
        chosen = cb(PlannedTask(title="x"), RoutingContext(), _candidates())
    assert chosen == "engineer"


def test_tiebreak_returns_fallback_when_claude_bin_missing() -> None:
    cb = make_claude_cli_tiebreak(claude_bin="/nope/missing/claude", fallback_persona="engineer")
    with patch("neumann.router.tiebreaks.shutil.which", return_value=None), \
         patch("neumann.router.tiebreaks.os.path.isfile", return_value=False):
        chosen = cb(PlannedTask(title="x"), RoutingContext(), _candidates())
    assert chosen == "engineer"


def test_tiebreak_filters_fallback_sentinel_from_candidates() -> None:
    """The FALLBACK_SENTINEL passed in candidates list must never be returned."""
    candidates = [{"id": FALLBACK_SENTINEL}, *_candidates()]
    cb = make_claude_cli_tiebreak()
    with patch("neumann.router.tiebreaks.shutil.which", return_value="/opt/homebrew/bin/claude"), \
         patch("neumann.router.tiebreaks.subprocess.run",
               side_effect=_stub_run('{"persona_id": "engineer"}')):
        chosen = cb(PlannedTask(title="x"), RoutingContext(), candidates)
    assert chosen == "engineer"


def test_tiebreak_integrates_with_routing_fallback() -> None:
    """End-to-end: PersonaSelector returns fallback sentinel → RoutingFallback
    invokes the tiebreak callback → callback returns a real persona."""

    selector = PersonaSelector()
    sel = selector.select(TaskType.UNKNOWN, RoutingContext())  # → FALLBACK_SENTINEL
    assert sel.persona == FALLBACK_SENTINEL

    cb = make_claude_cli_tiebreak()
    fallback = RoutingFallback(tiebreak_callback=cb)
    with patch("neumann.router.tiebreaks.shutil.which", return_value="/opt/homebrew/bin/claude"), \
         patch("neumann.router.tiebreaks.subprocess.run",
               side_effect=_stub_run('{"persona_id": "qa-engineer"}')):
        resolved = fallback.resolve(sel, task=PlannedTask(title="audit the test suite"), context=RoutingContext())
    assert resolved.persona == "qa-engineer"
    assert resolved.fallback_used
    assert any("tiebreak" in line for line in resolved.trace)
