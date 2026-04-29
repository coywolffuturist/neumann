"""RoutingFallback — handles fallback persona selection when dispatch fails.

Two branches:

1. **Generic fallback**: route to ``engineer`` (the most flexible preset).
   Appropriate when ``task_type=unknown`` was just a catch-all classification
   on a clearly engineering task.
2. **LLM tie-break**: invoke a tiny LLM call with the planned task + the
   list of available personas + their descriptions, and let it decide.
   This is the *only* place LLM judgment enters the routing pipeline.

The choice is configurable. Default is ``"generic"`` — no LLM call — which
keeps the kernel deterministic out of the box. Callers that want LLM
tie-breaks pass a ``tiebreak_callback``.

Mirrors ``neumann.formatters.fallback.FallbackHandler``.
"""
from __future__ import annotations

from typing import Callable

from .registry import PersonaRegistry
from .types import (
    FALLBACK_SENTINEL,
    PersonaDecision,
    PersonaId,
    PlannedTask,
    RoutingContext,
    TaskType,
)

GENERIC_FALLBACK_PERSONA: PersonaId = "engineer"

TiebreakCallback = Callable[[PlannedTask, RoutingContext, list[dict]], PersonaId]


class RoutingFallback:
    def __init__(
        self,
        generic_persona: PersonaId = GENERIC_FALLBACK_PERSONA,
        tiebreak_callback: TiebreakCallback | None = None,
        registry: PersonaRegistry | None = None,
    ) -> None:
        self._generic = generic_persona
        self._tiebreak = tiebreak_callback
        self._registry = registry or PersonaRegistry()

    # ── public ────────────────────────────────────────────────

    def resolve(
        self,
        decision: PersonaDecision,
        task: PlannedTask,
        context: RoutingContext,
    ) -> PersonaDecision:
        """If decision is the fallback sentinel, replace with a real persona.

        Otherwise return the decision unchanged.
        """
        if decision.persona != FALLBACK_SENTINEL:
            return decision

        if self._tiebreak is not None:
            available_records = [
                {"id": pid, **(self._registry.get(pid) or {})}
                for pid in (context.available_personas or self._registry.list_ids())
            ]
            chosen = self._tiebreak(task, context, available_records)
            return PersonaDecision(
                persona=chosen,
                task_type=decision.task_type,
                matched_rule_priority=decision.matched_rule_priority,
                dispatch_priority=decision.dispatch_priority,
                fallback_used=True,
                trace=decision.trace + (f"tiebreak resolved → {chosen}",),
            )

        return PersonaDecision(
            persona=self._generic,
            task_type=decision.task_type,
            matched_rule_priority=decision.matched_rule_priority,
            dispatch_priority=decision.dispatch_priority,
            fallback_used=True,
            trace=decision.trace + (f"generic fallback → {self._generic}",),
        )
