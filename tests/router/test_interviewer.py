"""Tests for the Interviewer module — interview loop + intent validation."""
from __future__ import annotations

import pytest

from neumann.router import (
    CLIInterviewer,
    ConfirmedIntent,
    InterviewIncomplete,
    MockInterviewer,
    validate_intent,
)


# ── validate_intent (pure schema gate) ───────────────────────────


def test_valid_intent_passes() -> None:
    intent = ConfirmedIntent(
        raw_prompt="add /healthz",
        confirmed_intent="Add /healthz endpoint returning {ok:true}",
        target_repo="pakt-world/paktsuite-v2",
        success_criteria=("GET /healthz returns 200 with body {ok:true}",),
        human_approved=True,
    )
    result = validate_intent(intent, allowed_orgs=("pakt-world",))
    assert result.valid


def test_empty_confirmed_intent_fails() -> None:
    intent = ConfirmedIntent(raw_prompt="x", confirmed_intent="", target_repo="a/b")
    result = validate_intent(intent)
    assert not result.valid
    assert "confirmed_intent is empty" in result.reason


def test_missing_target_repo_fails() -> None:
    intent = ConfirmedIntent(raw_prompt="x", confirmed_intent="x", target_repo="")
    result = validate_intent(intent)
    assert not result.valid
    assert "target_repo is missing" in result.reason


def test_malformed_target_repo_fails() -> None:
    intent = ConfirmedIntent(raw_prompt="x", confirmed_intent="x", target_repo="just-a-name")
    result = validate_intent(intent)
    assert not result.valid
    assert "valid owner/name" in result.reason


def test_target_repo_outside_allowlist_fails() -> None:
    intent = ConfirmedIntent(
        raw_prompt="x",
        confirmed_intent="x",
        target_repo="random-user/sketchy-repo",
        success_criteria=("anything",),
        human_approved=True,
    )
    result = validate_intent(intent, allowed_orgs=("pakt-world", "coywolffuturist"))
    assert not result.valid
    assert "not in allowed orgs" in result.reason


def test_missing_success_criteria_fails() -> None:
    intent = ConfirmedIntent(
        raw_prompt="x",
        confirmed_intent="x",
        target_repo="pakt-world/paktsuite-v2",
        human_approved=True,
    )
    result = validate_intent(intent, allowed_orgs=("pakt-world",))
    assert not result.valid
    assert "success_criterion" in result.reason


def test_unapproved_intent_fails() -> None:
    intent = ConfirmedIntent(
        raw_prompt="x",
        confirmed_intent="x",
        target_repo="pakt-world/paktsuite-v2",
        success_criteria=("done when shipped",),
        human_approved=False,
    )
    result = validate_intent(intent, allowed_orgs=("pakt-world",))
    assert not result.valid
    assert "has not yet approved" in result.reason


# ── MockInterviewer ──────────────────────────────────────────────


def test_mock_interviewer_returns_registered_fixture() -> None:
    canned = ConfirmedIntent(
        raw_prompt="placeholder",
        confirmed_intent="Add /healthz endpoint returning {ok:true}",
        target_repo="pakt-world/paktsuite-v2",
        success_criteria=("GET /healthz returns 200",),
        human_approved=True,
    )
    interviewer = MockInterviewer().register("healthz", canned)
    out = interviewer.interview("Can you add a /healthz endpoint?")
    assert out.target_repo == "pakt-world/paktsuite-v2"
    assert out.human_approved
    # raw_prompt is restamped to the actual call
    assert out.raw_prompt == "Can you add a /healthz endpoint?"


def test_mock_interviewer_default_when_no_match() -> None:
    canned = ConfirmedIntent(
        raw_prompt="placeholder",
        confirmed_intent="Default fallback intent",
        target_repo="pakt-world/paktsuite-v2",
        success_criteria=("default success",),
        human_approved=True,
    )
    interviewer = MockInterviewer().set_default(canned)
    out = interviewer.interview("Some unrelated prompt")
    assert out.confirmed_intent == "Default fallback intent"


def test_mock_interviewer_raises_without_fixture_or_default() -> None:
    interviewer = MockInterviewer()
    with pytest.raises(RuntimeError, match="no fixture matched"):
        interviewer.interview("anything")


# ── CLIInterviewer (with stubbed I/O) ────────────────────────────


def _scripted_io(answers: list[str], log: list[str]):
    """Helpers that mimic input()/print() for a scripted CLI run."""

    answer_iter = iter(answers)

    def fake_read(prompt: str) -> str:
        try:
            ans = next(answer_iter)
        except StopIteration:
            raise AssertionError(f"CLI asked more questions than scripted answers ({prompt})")
        log.append(f">> {ans}")
        return ans

    def fake_write(msg: str) -> None:
        log.append(msg)

    return fake_read, fake_write


def test_cli_interviewer_happy_path() -> None:
    log: list[str] = []
    answers = [
        "pakt-world/paktsuite-v2",                              # target_repo
        "GET /healthz returns 200 with {ok:true}",              # success_criteria
        "yes",                                                   # human_approved
    ]
    read, write = _scripted_io(answers, log)
    interviewer = CLIInterviewer(
        allowed_orgs=("pakt-world",),
        prompt_io=read,
        write_io=write,
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert out.target_repo == "pakt-world/paktsuite-v2"
    assert out.success_criteria == ("GET /healthz returns 200 with {ok:true}",)
    assert out.human_approved
    assert len(out.transcript) >= 3


def test_cli_interviewer_re_asks_repo_when_outside_allowlist() -> None:
    """Wrong-org repo answer triggers re-ask; valid retry completes the interview."""
    log: list[str] = []
    answers = [
        "random-org/random-repo",                  # wrong org → loop re-asks
        "pakt-world/paktsuite-v2",                 # correct retry
        "GET /healthz returns 200 with {ok:true}", # success_criteria
        "approve",                                 # human_approved
    ]
    read, write = _scripted_io(answers, log)
    interviewer = CLIInterviewer(
        allowed_orgs=("pakt-world",),
        prompt_io=read,
        write_io=write,
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert out.target_repo == "pakt-world/paktsuite-v2"
    assert out.human_approved


def test_cli_interviewer_raises_when_max_rounds_exhausted() -> None:
    """Persistent bad answers exhaust the loop and raise InterviewIncomplete."""
    log: list[str] = []
    # 8 wrong-org answers — never satisfies the allowlist check.
    answers = ["bad-org/x"] * 8
    read, write = _scripted_io(answers, log)
    interviewer = CLIInterviewer(
        allowed_orgs=("pakt-world",),
        prompt_io=read,
        write_io=write,
    )
    with pytest.raises(InterviewIncomplete):
        interviewer.interview("Add /healthz endpoint")


def test_cli_interviewer_refine_path() -> None:
    """User can refine the confirmed_intent at the approval gate."""
    log: list[str] = []
    answers = [
        "pakt-world/paktsuite-v2",
        "Endpoint returns 200",
        "refine: should also include Cache-Control header",   # refines confirmed_intent
        "yes",                                                  # approves the refined intent
    ]
    read, write = _scripted_io(answers, log)
    interviewer = CLIInterviewer(
        allowed_orgs=("pakt-world",),
        prompt_io=read,
        write_io=write,
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert "Cache-Control header" in out.confirmed_intent
    assert out.human_approved
