"""Planner protocol — the contract between an LLM and the Neumann router.

The router doesn't ship an LLM. It defines the interface (``Planner``) and
provides a ``MockPlanner`` for tests + offline development. Real planners
(Claude, Sonnet, etc.) plug in by implementing ``plan(prompt)`` to return
a ``Plan``.

Soul + instructionsText for the canonical Planner persona live in
``personas/planner.json``. A real LLM-backed implementation should load
that file and prepend it to the system prompt before calling the model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .types import Plan, PlannedTask

PLANNER_SPEC_PATH = Path(__file__).parent / "personas" / "planner.json"


@runtime_checkable
class Planner(Protocol):
    """Anything that turns a mission prompt into a structured Plan."""

    def plan(self, prompt: str, context: dict[str, Any] | None = None) -> Plan: ...


def load_planner_spec() -> dict[str, Any]:
    """Load the Planner persona JSON (soul + instructionsText) for prompt-building."""
    with open(PLANNER_SPEC_PATH) as f:
        return json.load(f)


@dataclass
class _MockPlannerEntry:
    match_substrings: tuple[str, ...]
    plan: Plan


class MockPlanner:
    """Deterministic mock planner for tests + offline development.

    Register fixture plans keyed by substring matches against the prompt.
    The first matching fixture wins. If nothing matches, returns a single-task
    plan derived from the prompt.

    Used by the test suite. NOT for production routing.
    """

    def __init__(self) -> None:
        self._fixtures: list[_MockPlannerEntry] = []

    def register(self, match_substrings: tuple[str, ...], plan: Plan) -> "MockPlanner":
        self._fixtures.append(_MockPlannerEntry(match_substrings=tuple(s.lower() for s in match_substrings), plan=plan))
        return self

    def plan(self, prompt: str, context: dict[str, Any] | None = None) -> Plan:
        lower = prompt.lower()
        for entry in self._fixtures:
            if all(sub in lower for sub in entry.match_substrings):
                return entry.plan
        # Default: single-task plan derived from the prompt.
        return Plan(
            mission_title=prompt.strip()[:80],
            summary="(MockPlanner default — no fixture matched)",
            assumptions=("Planner had no fixture for this prompt; treating as a single task.",),
            tasks=(PlannedTask.from_prompt(prompt),),
        )
