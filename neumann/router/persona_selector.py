"""PersonaSelector — pure dispatch from (TaskType, RoutingContext) → Persona.

Dispatch table loaded from ``rules/persona_dispatch.json``. Wildcards (``"*"``)
match any value. Priority breaks ties when multiple rows match.

Mirrors the behavior of ``neumann.selector.FormatSelector`` — same lookup
shape, different domain.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import (
    PersonaId,
    PersonaDecision,
    PlannedTask,
    RoutingContext,
    TaskType,
    FALLBACK_SENTINEL,
)

DEFAULT_DISPATCH_PATH = Path(__file__).parent / "rules" / "persona_dispatch.json"


class PersonaSelector:
    def __init__(self, dispatch_path: Path | str | None = None) -> None:
        path = Path(dispatch_path) if dispatch_path else DEFAULT_DISPATCH_PATH
        self._table = self._load(path)

    # ── public ────────────────────────────────────────────────

    def select(
        self,
        task_type: TaskType,
        context: RoutingContext,
        task: PlannedTask | None = None,
        type_match_metadata: dict[str, Any] | None = None,
    ) -> PersonaDecision:
        """Return the best matching ``PersonaDecision``.

        ``task`` and ``type_match_metadata`` are passed through purely for
        building the trace; selection logic only consults task_type + context.
        """
        candidates = [
            row
            for row in self._table
            if self._matches(row, task_type.value, context.project_type)
        ]
        if not candidates:
            return PersonaDecision(
                persona=FALLBACK_SENTINEL,
                task_type=task_type,
                matched_rule_priority=(type_match_metadata or {}).get("priority", -1),
                dispatch_priority=99,
                fallback_used=True,
                trace=(
                    f"task_type={task_type.value}",
                    f"context.project_type={context.project_type}",
                    "no dispatch row matched",
                    "→ FALLBACK_SENTINEL",
                ),
            )

        best = min(candidates, key=lambda r: r["priority"])
        persona = best["persona"]

        # Honor available_personas filter — if the chosen persona is not
        # available, downgrade to next-priority candidate that is.
        if context.available_personas and persona != FALLBACK_SENTINEL and persona not in context.available_personas:
            ordered = sorted(candidates, key=lambda r: r["priority"])
            for row in ordered:
                if row["persona"] == FALLBACK_SENTINEL or row["persona"] in context.available_personas:
                    best = row
                    persona = row["persona"]
                    break
            else:
                # No available candidate at all → fallback.
                return PersonaDecision(
                    persona=FALLBACK_SENTINEL,
                    task_type=task_type,
                    matched_rule_priority=(type_match_metadata or {}).get("priority", -1),
                    dispatch_priority=99,
                    fallback_used=True,
                    trace=(
                        f"task_type={task_type.value}",
                        f"context.project_type={context.project_type}",
                        f"top match {best['persona']} not in available_personas",
                        "→ FALLBACK_SENTINEL",
                    ),
                )

        return PersonaDecision(
            persona=persona,
            task_type=task_type,
            matched_rule_priority=(type_match_metadata or {}).get("priority", -1),
            dispatch_priority=best["priority"],
            fallback_used=(persona == FALLBACK_SENTINEL),
            trace=(
                f"task_type={task_type.value}",
                f"context.project_type={context.project_type}",
                f"matched dispatch row priority={best['priority']} → {persona}",
            ),
        )

    # ── private ───────────────────────────────────────────────

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _matches(row: dict[str, Any], task_type: str, context: str) -> bool:
        t_match = row["type"] in (task_type, "*")
        c_match = row["context"] in (context, "*")
        return t_match and c_match
