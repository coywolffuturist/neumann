"""Core types for the Neumann router.

These are pure data classes. No logic, no I/O. Mirrors the style of
``neumann/types.py`` for the upstream pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Shape(str, Enum):
    """Top-level shape of an incoming user prompt."""

    SINGLE_TASK = "single-task"
    MISSION = "mission"


class TaskType(str, Enum):
    """Functional category of a planned task. Drives persona selection.

    Order roughly tracks the priority bands in ``task_type_rules.json``.
    """

    VERIFICATION = "verification"
    SECURITY = "security"
    TECH_ARCHITECTURE = "tech-architecture"
    STRATEGY = "strategy"
    FINANCE = "finance"
    MARKETING = "marketing"
    FRONTEND = "frontend"
    BACKEND = "backend"
    FULLSTACK = "fullstack"
    PERFORMANCE = "performance"
    DOCUMENTATION = "documentation"
    BUGFIX = "bugfix"
    IMPLEMENTATION = "implementation"
    UNKNOWN = "unknown"


# PersonaId is a free-form string so callers can extend Fusion's preset ids
# (ceo, cto, cmo, cfo, engineer, backend-engineer, frontend-engineer,
# fullstack-engineer, qa-engineer) with custom personas without changing this file.
PersonaId = str

# Reserved sentinel for the dispatch table — fallback handler decides at runtime.
FALLBACK_SENTINEL: PersonaId = "__fallback__"


@dataclass(frozen=True)
class PlannedTask:
    """A single unit of work the router classifies and dispatches.

    Produced by the LLM Planner OR synthesized from a single-task prompt.
    All fields except ``title`` are optional but more fields = better routing.
    """

    title: str
    description: str = ""
    type_hints: tuple[str, ...] = ()
    target_files: tuple[str, ...] = ()
    acceptance_criteria: str = ""
    depends_on: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_prompt(cls, prompt: str) -> "PlannedTask":
        """Wrap a raw single-task prompt as a PlannedTask without invoking a planner."""
        return cls(title=prompt.strip(), description=prompt.strip())

    def match_text(self, fields: tuple[str, ...]) -> str:
        """Concatenate the requested fields for regex matching."""
        parts: list[str] = []
        for f in fields:
            if f == "title":
                parts.append(self.title)
            elif f == "description":
                parts.append(self.description)
            elif f == "type_hints":
                parts.append(" ".join(self.type_hints))
            elif f == "target_files":
                parts.append("\n".join(self.target_files))
            elif f == "acceptance_criteria":
                parts.append(self.acceptance_criteria)
        return "\n".join(p for p in parts if p)


@dataclass(frozen=True)
class Plan:
    """Output of the LLM Planner for a mission-shape prompt."""

    mission_title: str
    summary: str = ""
    assumptions: tuple[str, ...] = ()
    tasks: tuple[PlannedTask, ...] = ()


@dataclass(frozen=True)
class RoutingContext:
    """Environmental context that influences persona selection.

    Set by the ContextResolver before the dispatch table is consulted.
    """

    project_type: str = "*"  # frontend-project | backend-project | test-project | architecture | *
    available_personas: tuple[PersonaId, ...] = ()
    persona_load: dict[PersonaId, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShapeDecision:
    shape: Shape
    matched_rule_priority: int
    matched_rule_note: str
    sentence_count: int = 0


@dataclass(frozen=True)
class PersonaDecision:
    persona: PersonaId
    task_type: TaskType
    matched_rule_priority: int
    dispatch_priority: int
    fallback_used: bool
    trace: tuple[str, ...]


@dataclass(frozen=True)
class RoutingTrace:
    """Audit log of one routing decision — replayable, observable."""

    input_hash: str
    shape_decision: ShapeDecision | None
    task: PlannedTask
    context: RoutingContext
    persona_decision: PersonaDecision
    duration_ms: float


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str = ""
    severity: str = "ok"  # ok | warn | error | fatal
