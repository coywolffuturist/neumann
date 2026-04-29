"""Tests for TaskTypeClassifier — structured PlannedTask → TaskType."""
from __future__ import annotations

import pytest

from neumann.router import PlannedTask, TaskType, TaskTypeClassifier


@pytest.fixture
def classifier() -> TaskTypeClassifier:
    return TaskTypeClassifier()


def test_verification_wins_over_implementation(classifier: TaskTypeClassifier) -> None:
    """A task that says 'verify the typo fix' should route to QA, not engineer."""
    task = PlannedTask(
        title="Verify the typo fix landed correctly",
        description="Run agent-browser against /docs and confirm the typo is gone.",
        type_hints=("browser-check",),
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.VERIFICATION


def test_target_files_drive_frontend_classification(classifier: TaskTypeClassifier) -> None:
    """A .tsx file is a far stronger signal than prose. File path wins."""
    task = PlannedTask(
        title="Tweak the rendering",
        description="Adjust how the page renders.",
        target_files=("src/components/Header.tsx",),
    )
    task_type, meta = classifier.classify(task)
    assert task_type == TaskType.FRONTEND
    assert "target_files" in meta["fields"]


def test_target_files_drive_backend_classification(classifier: TaskTypeClassifier) -> None:
    task = PlannedTask(
        title="Tighten validation",
        description="Validate input properly.",
        target_files=("app/server.js", "db/migrations/0042.sql"),
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.BACKEND


def test_security_keywords(classifier: TaskTypeClassifier) -> None:
    task = PlannedTask(
        title="Sanitize user input on /api/login",
        description="Prevent SQL injection and XSS in the login endpoint.",
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.SECURITY


def test_marketing_keywords(classifier: TaskTypeClassifier) -> None:
    task = PlannedTask(
        title="Rewrite the landing-page copy",
        description="Reframe the messaging around audience growth.",
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.MARKETING


def test_strategy_keywords(classifier: TaskTypeClassifier) -> None:
    task = PlannedTask(
        title="Decide between Postgres and DynamoDB",
        description="Prioritize options against our north-star roadmap.",
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.STRATEGY


def test_finance_keywords(classifier: TaskTypeClassifier) -> None:
    task = PlannedTask(
        title="Estimate operational cost of switching to Bedrock",
        description="Quantify infrastructure cost vs current setup.",
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.FINANCE


def test_documentation_keywords(classifier: TaskTypeClassifier) -> None:
    task = PlannedTask(
        title="Write the AGENTS.md for the new repo",
        description="Document project layout, paths, and conventions.",
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.DOCUMENTATION


def test_bugfix_keywords(classifier: TaskTypeClassifier) -> None:
    task = PlannedTask(
        title="Fix crash on logout",
        description="Users report a stack trace on logout.",
    )
    task_type, _ = classifier.classify(task)
    assert task_type == TaskType.BUGFIX


def test_unknown_falls_through(classifier: TaskTypeClassifier) -> None:
    """A task that matches no specific rule should hit catch-all unknown."""
    task = PlannedTask(title="Hello world")
    task_type, meta = classifier.classify(task)
    assert task_type == TaskType.UNKNOWN
    assert meta["priority"] == 99
