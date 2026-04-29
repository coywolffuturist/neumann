"""RoutingValidator — pre-flight gate before a routing decision is acted upon.

Deterministic. Catches:
- Persona doesn't exist in the registry
- Persona is currently disabled
- Persona has saturated its concurrent-task budget (when context tracks load)

Mirrors ``neumann.validator.SchemaValidator``.
"""
from __future__ import annotations

from typing import Any

from .registry import PersonaRegistry
from .types import (
    FALLBACK_SENTINEL,
    PersonaDecision,
    RoutingContext,
    ValidationResult,
)

DEFAULT_PERSONA_LOAD_LIMIT = 4


class RoutingValidator:
    def __init__(
        self,
        registry: PersonaRegistry | None = None,
        load_limit: int = DEFAULT_PERSONA_LOAD_LIMIT,
    ) -> None:
        self._registry = registry or PersonaRegistry()
        self._load_limit = load_limit

    # ── public ────────────────────────────────────────────────

    def validate(self, decision: PersonaDecision, context: RoutingContext) -> ValidationResult:
        # Fallback sentinel is always "valid" — its handling happens downstream.
        if decision.persona == FALLBACK_SENTINEL:
            return ValidationResult(valid=True, reason="fallback sentinel — handler decides", severity="ok")

        record = self._registry.get(decision.persona)
        if record is None:
            return ValidationResult(
                valid=False,
                reason=f"persona '{decision.persona}' not registered",
                severity="error",
            )

        if not record.get("enabled", True):
            return ValidationResult(
                valid=False,
                reason=f"persona '{decision.persona}' is disabled",
                severity="warn",
            )

        load = context.persona_load.get(decision.persona, 0)
        if load >= self._load_limit:
            return ValidationResult(
                valid=False,
                reason=f"persona '{decision.persona}' at load {load} ≥ limit {self._load_limit}",
                severity="warn",
            )

        return ValidationResult(valid=True, severity="ok")
