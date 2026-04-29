"""End-to-end tests for RouterPipeline with the Interviewer wired in."""
from __future__ import annotations

import pytest

from neumann.router import (
    ConfirmedIntent,
    MockInterviewer,
    MockPlanner,
    Plan,
    PlannedTask,
    RouterPipeline,
    Shape,
    TaskType,
)


def test_pipeline_with_interviewer_passes_intent_to_planner() -> None:
    """The Interviewer's ConfirmedIntent flows into the Planner and onto the Plan."""
    confirmed = ConfirmedIntent(
        raw_prompt="placeholder",
        confirmed_intent="Build the whole signup flow with email verification only (no SSO)",
        target_repo="pakt-world/paktsuite-v2",
        success_criteria=("Users can sign up, verify email, log in",),
        constraints=("No SSO yet",),
        out_of_scope=("password reset",),
        human_approved=True,
    )

    plan_fixture = Plan(
        mission_title="Signup flow",
        summary="Email-only signup with verify.",
        tasks=(
            PlannedTask(
                title="Add /api/signup endpoint",
                description="Email + password signup endpoint.",
                target_files=("app/server.js",),
                type_hints=("api", "endpoint"),
            ),
            PlannedTask(
                title="Add Signup form component",
                description="UI for the signup endpoint.",
                target_files=("app/public/components/Signup.tsx",),
                type_hints=("component", "form"),
            ),
            PlannedTask(
                title="Smoke-verify the signup flow",
                description="Browser-test the happy path.",
                type_hints=("verification",),
            ),
        ),
    )

    interviewer = MockInterviewer().set_default(confirmed)
    planner = MockPlanner().register(("signup",), plan_fixture)

    pipeline = RouterPipeline(planner=planner, interviewer=interviewer)
    result = pipeline.process("Build the whole signup flow")

    assert result.shape_decision.shape == Shape.MISSION
    assert result.confirmed_intent is not None
    assert result.confirmed_intent.target_repo == "pakt-world/paktsuite-v2"
    assert result.plan is not None
    # The Plan should be stamped with the ConfirmedIntent that authorized it.
    assert result.plan.confirmed_intent is not None
    assert result.plan.confirmed_intent.human_approved
    # Routing still happens per task as before.
    personas = [r.persona_decision.persona for r in result.routes]
    assert personas == ["backend-engineer", "frontend-engineer", "qa-engineer"]


def test_pipeline_without_interviewer_v1_behavior() -> None:
    """When no Interviewer is wired, pipeline behaves like v1: prompt → plan → route."""
    pipeline = RouterPipeline()  # no interviewer
    result = pipeline.process("Fix the typo in the README")
    assert result.confirmed_intent is None
    assert result.shape_decision.shape == Shape.SINGLE_TASK
    assert len(result.routes) == 1


def test_single_task_with_interview_uses_confirmed_intent_as_task_text() -> None:
    """A single-task prompt with an interview substitutes the confirmed_intent for routing."""
    confirmed = ConfirmedIntent(
        raw_prompt="placeholder",
        confirmed_intent="Add /api/healthz endpoint returning {ok:true} with 5xx error logging",
        target_repo="pakt-world/paktsuite-v2",
        success_criteria=("Endpoint returns 200 with body {ok:true}",),
        human_approved=True,
    )
    interviewer = MockInterviewer().set_default(confirmed)
    pipeline = RouterPipeline(interviewer=interviewer)

    result = pipeline.process("add a healthz")
    # Single-task path because shape rules treat short imperatives as single-task.
    assert result.shape_decision.shape == Shape.SINGLE_TASK
    assert result.confirmed_intent is not None
    assert len(result.routes) == 1
    # The confirmed_intent text drove the task title (richer than the raw 4-word prompt).
    assert "healthz" in result.routes[0].task.title.lower()
