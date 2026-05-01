"""Tests for PersonaSelector — (TaskType, RoutingContext) → Persona."""
from __future__ import annotations

import pytest

from neumann.router import (
    FALLBACK_SENTINEL,
    PersonaSelector,
    PlannedTask,
    RoutingContext,
    TaskType,
)


@pytest.fixture
def selector() -> PersonaSelector:
    return PersonaSelector()


def _ctx(project_type: str = "*", available: tuple[str, ...] = ()) -> RoutingContext:
    return RoutingContext(project_type=project_type, available_personas=available)


def test_verification_routes_to_qa(selector: PersonaSelector) -> None:
    decision = selector.select(TaskType.VERIFICATION, _ctx())
    assert decision.persona == "qa-engineer"
    assert not decision.fallback_used


def test_security_in_architecture_context_routes_to_cto(selector: PersonaSelector) -> None:
    decision = selector.select(TaskType.SECURITY, _ctx(project_type="architecture"))
    assert decision.persona == "cto"


def test_security_default_routes_to_backend(selector: PersonaSelector) -> None:
    decision = selector.select(TaskType.SECURITY, _ctx(project_type="backend-project"))
    assert decision.persona == "backend-engineer"


def test_strategy_routes_to_ceo(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.STRATEGY, _ctx()).persona == "ceo"


def test_finance_routes_to_cfo(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.FINANCE, _ctx()).persona == "cfo"


def test_marketing_routes_to_cmo(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.MARKETING, _ctx()).persona == "cmo"


def test_tech_architecture_routes_to_cto(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.TECH_ARCHITECTURE, _ctx()).persona == "cto"


def test_frontend_routes_to_frontend_engineer(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.FRONTEND, _ctx()).persona == "frontend-engineer"


def test_backend_routes_to_backend_engineer(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.BACKEND, _ctx()).persona == "backend-engineer"


def test_fullstack_routes_to_fullstack_engineer(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.FULLSTACK, _ctx()).persona == "fullstack-engineer"


def test_performance_in_frontend_project_routes_to_frontend(selector: PersonaSelector) -> None:
    decision = selector.select(TaskType.PERFORMANCE, _ctx(project_type="frontend-project"))
    assert decision.persona == "frontend-engineer"


def test_performance_default_routes_to_backend(selector: PersonaSelector) -> None:
    decision = selector.select(TaskType.PERFORMANCE, _ctx(project_type="backend-project"))
    assert decision.persona == "backend-engineer"


def test_documentation_in_test_project_routes_to_qa(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.DOCUMENTATION, _ctx(project_type="test-project")).persona == "qa-engineer"


def test_documentation_default_routes_to_engineer(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.DOCUMENTATION, _ctx()).persona == "engineer"


def test_bugfix_in_frontend_project_routes_to_frontend(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.BUGFIX, _ctx(project_type="frontend-project")).persona == "frontend-engineer"


def test_bugfix_default_routes_to_engineer(selector: PersonaSelector) -> None:
    assert selector.select(TaskType.BUGFIX, _ctx()).persona == "engineer"


def test_unknown_returns_fallback_sentinel(selector: PersonaSelector) -> None:
    decision = selector.select(TaskType.UNKNOWN, _ctx())
    assert decision.persona == FALLBACK_SENTINEL
    assert decision.fallback_used


def test_unavailable_persona_falls_back(selector: PersonaSelector) -> None:
    """If selected persona isn't in available_personas, dispatch downgrades."""
    # Verification's only candidate is qa-engineer. If qa-engineer is removed
    # from available_personas, dispatch returns fallback.
    decision = selector.select(
        TaskType.VERIFICATION,
        _ctx(available=("engineer", "backend-engineer")),
    )
    assert decision.persona == FALLBACK_SENTINEL
    assert decision.fallback_used


def _ctx_col(column: str, project_type: str = "*") -> RoutingContext:
    return RoutingContext(project_type=project_type, column=column)


def test_in_review_column_routes_to_qa_regardless_of_task_type(selector: PersonaSelector) -> None:
    """Pre-merge QA gate: any task entering In Review goes to qa persona."""
    for task_type in (
        TaskType.FRONTEND,
        TaskType.BACKEND,
        TaskType.BUGFIX,
        TaskType.IMPLEMENTATION,
        TaskType.STRATEGY,
    ):
        decision = selector.select(task_type, _ctx_col("in-review"))
        assert decision.persona == "qa", f"task_type={task_type.value} did not route to qa in In Review"
        assert decision.dispatch_priority == 0
        assert not decision.fallback_used


def test_in_review_dispatch_does_not_leak_to_other_columns(selector: PersonaSelector) -> None:
    """Initial dispatch (no column) must not route to qa just because the rule exists."""
    decision = selector.select(TaskType.FRONTEND, _ctx_col(""))
    assert decision.persona == "frontend-engineer"
    assert decision.dispatch_priority == 1


def test_other_columns_use_normal_task_type_dispatch(selector: PersonaSelector) -> None:
    """Columns other than in-review do not trigger the QA override."""
    for column in ("planning", "todo", "in-progress", "done"):
        decision = selector.select(TaskType.FRONTEND, _ctx_col(column))
        assert decision.persona == "frontend-engineer", (
            f"column={column} unexpectedly routed away from frontend-engineer"
        )
