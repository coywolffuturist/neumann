"""Tests for ShapeClassifier — mission vs single-task."""
from __future__ import annotations

import pytest

from neumann.router import Shape, ShapeClassifier


@pytest.fixture
def classifier() -> ShapeClassifier:
    return ShapeClassifier()


@pytest.mark.parametrize(
    "prompt",
    [
        "Fix the typo in the README",
        "Bump express to latest",
        "Add a /healthz endpoint",
        "Rename getUser to fetchUser",
        "Update the dashboard color",
    ],
)
def test_short_imperatives_are_single_task(classifier: ShapeClassifier, prompt: str) -> None:
    decision = classifier.classify(prompt)
    assert decision.shape == Shape.SINGLE_TASK


@pytest.mark.parametrize(
    "prompt",
    [
        "Build the whole signup flow",
        "Implement the entire payment pipeline end to end",
        "Create the full onboarding experience for new users",
    ],
)
def test_explicit_multi_piece_intent_is_mission(classifier: ShapeClassifier, prompt: str) -> None:
    decision = classifier.classify(prompt)
    assert decision.shape == Shape.MISSION
    assert decision.matched_rule_priority == 1


def test_architecture_overhaul_is_mission(classifier: ShapeClassifier) -> None:
    decision = classifier.classify("Refactor the architecture of the auth service")
    assert decision.shape == Shape.MISSION
    assert decision.matched_rule_priority == 2


def test_bootstrap_new_service_is_mission(classifier: ShapeClassifier) -> None:
    decision = classifier.classify("Bootstrap a new microservice for invoicing")
    assert decision.shape == Shape.MISSION
    assert decision.matched_rule_priority == 3


def test_three_substantive_sentences_is_mission(classifier: ShapeClassifier) -> None:
    prompt = (
        "We need to add user signup. The flow should send a verification email. "
        "After verification, users land on the onboarding tour."
    )
    decision = classifier.classify(prompt)
    assert decision.shape == Shape.MISSION
    assert decision.matched_rule_priority == 10
    # Three sentences → 2 boundaries → reported as 3.
    assert decision.sentence_count >= 3


def test_two_short_sentences_is_single_task(classifier: ShapeClassifier) -> None:
    """Two sentences alone aren't enough to escalate — we want >= 3."""
    decision = classifier.classify("Fix the typo. The word 'queery' should be 'query'.")
    assert decision.shape == Shape.SINGLE_TASK


def test_catch_all_always_resolves(classifier: ShapeClassifier) -> None:
    """No prompt — even gibberish — should fall through cleanly to single-task."""
    decision = classifier.classify("???")
    assert decision.shape == Shape.SINGLE_TASK
    assert decision.matched_rule_priority == 99
