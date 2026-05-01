"""End-to-end tests for RouterPipeline.

Pipes a raw user prompt through shape → (planner OR direct) → classify →
context → select → fallback → validate. These are the "golden path" tests.
"""
from __future__ import annotations

import pytest

from neumann.router import (
    FALLBACK_SENTINEL,
    MockPlanner,
    Plan,
    PlannedTask,
    RouterPipeline,
    Shape,
    TaskType,
)


@pytest.fixture
def pipeline() -> RouterPipeline:
    return RouterPipeline()


def test_single_task_prompt_routes_directly(pipeline: RouterPipeline) -> None:
    result = pipeline.process("Fix the typo in the README")
    assert result.shape_decision.shape == Shape.SINGLE_TASK
    assert result.plan is None
    assert len(result.routes) == 1
    # 'README' is in the doc rules; falls through to documentation/engineer
    # OR matches 'fix' as bugfix. Either way it's not a fallback.
    persona = result.routes[0].persona_decision.persona
    assert persona in {"engineer", "frontend-engineer", "backend-engineer", "qa-engineer"}


def test_single_task_with_target_files_routes_to_specialist() -> None:
    pipeline = RouterPipeline()
    task = PlannedTask(
        title="Add /api/healthz endpoint",
        description="Public health-check endpoint returning {ok:true}.",
        type_hints=("api", "endpoint", "monitoring"),
        target_files=("app/server.js",),
        acceptance_criteria="GET /api/healthz returns 200 with body {ok:true}",
    )
    trace = pipeline.route(task)
    assert trace.persona_decision.task_type == TaskType.BACKEND
    assert trace.persona_decision.persona == "backend-engineer"
    assert trace.context.project_type == "backend-project"
    assert not trace.persona_decision.fallback_used


def test_mission_prompt_invokes_planner_and_dispatches_per_task() -> None:
    fixture_plan = Plan(
        mission_title="Build the whole signup flow",
        summary="Email/password signup + verify + onboarding.",
        assumptions=("Email is the only supported identity for now.",),
        tasks=(
            PlannedTask(
                title="Add /api/signup endpoint",
                description="Accept email + password, hash, persist.",
                type_hints=("api", "endpoint", "auth"),
                target_files=("app/server.js", "db/migrations/0042.sql"),
                acceptance_criteria="POST returns 201 with session cookie",
            ),
            PlannedTask(
                title="Add Signup form component",
                description="Email + password fields with client-side validation.",
                type_hints=("component", "form", "responsive"),
                target_files=("app/public/components/Signup.tsx",),
                acceptance_criteria="Form posts to /api/signup, shows errors inline",
            ),
            PlannedTask(
                title="Smoke-verify the signup flow",
                description="Browser-test the happy path end-to-end.",
                type_hints=("verification", "browser-check"),
                target_files=(),
                acceptance_criteria="agent-browser open / fill / submit returns 201",
            ),
            PlannedTask(
                title="Write the launch announcement",
                description="Marketing copy for the new signup experience.",
                type_hints=("copy", "messaging", "growth"),
            ),
        ),
    )
    planner = MockPlanner().register(("signup", "whole"), fixture_plan)
    pipeline = RouterPipeline(planner=planner)

    result = pipeline.process("Build the whole signup flow with verification and a launch post")
    assert result.shape_decision.shape == Shape.MISSION
    assert result.plan is not None
    assert result.plan.mission_title == "Build the whole signup flow"
    personas = [r.persona_decision.persona for r in result.routes]
    assert personas == ["backend-engineer", "frontend-engineer", "qa-engineer", "cmo"]
    # All four routes must have non-fallback decisions.
    assert all(not r.persona_decision.fallback_used for r in result.routes)


def test_mock_planner_default_path() -> None:
    """When no fixture matches, MockPlanner returns a single-task plan derived from prompt."""
    planner = MockPlanner()
    plan = planner.plan("This is a totally unmapped prompt with no fixture.")
    assert plan.mission_title.startswith("This is a totally unmapped")
    assert len(plan.tasks) == 1


def test_unknown_task_falls_back_to_engineer() -> None:
    pipeline = RouterPipeline()
    task = PlannedTask(title="???")
    trace = pipeline.route(task)
    # task_type = UNKNOWN → dispatch sentinel → fallback handler → "engineer"
    assert trace.persona_decision.persona == "engineer"
    assert trace.persona_decision.fallback_used


def test_validation_passes_for_known_personas() -> None:
    pipeline = RouterPipeline()
    trace = pipeline.route(PlannedTask(title="Add /api/healthz endpoint"))
    # validation severity is appended to the trace
    assert any("validation: ok" in line for line in trace.persona_decision.trace)


def test_unavailable_persona_triggers_fallback_with_engineer_default() -> None:
    """Verification needs qa-engineer; if QA is unavailable, fallback handler fills in."""
    pipeline = RouterPipeline()
    task = PlannedTask(
        title="Verify the typo fix",
        description="Run a browser-check to confirm.",
    )
    trace = pipeline.route(task, env={"available_personas": ("engineer", "backend-engineer")})
    assert trace.persona_decision.fallback_used
    assert trace.persona_decision.persona == "engineer"  # generic fallback default


def test_input_hash_and_duration_are_populated() -> None:
    pipeline = RouterPipeline()
    trace = pipeline.route(PlannedTask(title="Add /api/healthz endpoint"))
    assert trace.input_hash.startswith("sha256:")
    assert trace.duration_ms >= 0
