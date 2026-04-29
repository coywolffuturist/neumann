"""Tests for ChatInterviewer and the Slack/Lucid/Web concrete subclasses.

Each subclass uses a queue-of-responses + list-of-sent-messages pair as the
transport. Real Slack/Lucid/Web integrations wire those callables to the
actual transport.
"""
from __future__ import annotations

import pytest

from neumann.router import (
    ChatInterviewer,
    InterviewIncomplete,
    LucidInterviewer,
    SlackInterviewer,
    WebInterviewer,
)


def _scripted_transport(answers: list[str]):
    """Returns (send, wait, sent_messages) — a queue-backed mock transport."""
    sent: list[str] = []
    answer_iter = iter(answers)

    def send(text: str) -> None:
        sent.append(text)

    def wait(timeout: float | None) -> str:
        try:
            return next(answer_iter)
        except StopIteration:
            raise TimeoutError("scripted answers exhausted")

    return send, wait, sent


# ── ChatInterviewer base behavior ────────────────────────────────


def test_chat_interviewer_happy_path() -> None:
    answers = [
        "pakt-world/paktsuite-v2",
        "GET /healthz returns 200",
        "ship it",
    ]
    send, wait, sent = _scripted_transport(answers)
    interviewer = ChatInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert out.target_repo == "pakt-world/paktsuite-v2"
    assert out.success_criteria == ("GET /healthz returns 200",)
    assert out.human_approved
    assert len(sent) == 3
    assert len(out.transcript) == 3


def test_chat_interviewer_recognizes_diverse_approvals() -> None:
    """The chat approval lexicon should accept emojis and Coywolf idioms."""
    for approval in ["yes", "approve", "lgtm", "👍", "✅"]:
        send, wait, _ = _scripted_transport([
            "pakt-world/paktsuite-v2",
            "Endpoint returns 200",
            approval,
        ])
        interviewer = ChatInterviewer(
            send_message=send,
            wait_for_response=wait,
            allowed_orgs=("pakt-world",),
        )
        out = interviewer.interview("Add endpoint")
        assert out.human_approved, f"approval phrase {approval!r} was not recognized"


def test_chat_interviewer_refine_path() -> None:
    answers = [
        "pakt-world/paktsuite-v2",
        "Endpoint returns 200",
        "refine: also include Cache-Control header",
        "yes",
    ]
    send, wait, _ = _scripted_transport(answers)
    interviewer = ChatInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert "Cache-Control header" in out.confirmed_intent
    assert out.human_approved


def test_chat_interviewer_timeout_raises_incomplete() -> None:
    """Empty queue → wait raises TimeoutError → interview raises InterviewIncomplete."""
    send, wait, _ = _scripted_transport([])  # no answers — first wait will timeout
    interviewer = ChatInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
    )
    with pytest.raises(InterviewIncomplete, match="timed out"):
        interviewer.interview("Add /healthz endpoint")


def test_chat_interviewer_stamps_approver_id() -> None:
    send, wait, _ = _scripted_transport([
        "pakt-world/paktsuite-v2",
        "x returns 200",
        "ship it",
    ])
    interviewer = ChatInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
        approver_id="U02ABC123",
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert out.human_approver_id == "U02ABC123"


# ── SlackInterviewer ─────────────────────────────────────────────


def test_slack_interviewer_formats_questions_with_wolf_prefix() -> None:
    send, wait, sent = _scripted_transport([
        "pakt-world/paktsuite-v2",
        "Endpoint returns 200",
        "ship it",
    ])
    interviewer = SlackInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
        approver_id="U02ABC123",
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert out.human_approved
    # All questions should have the Slack-flavored header instead of [router-interview]
    assert any("🐺 *Coywolf interview*" in m for m in sent)
    assert not any("[router-interview]" in m for m in sent)


def test_slack_interviewer_recognizes_ship_it_with_emoji() -> None:
    """🐺 reaction OR 'ship it' both work as approval."""
    for approval in ["🐺", "ship it", "shipit", "Ship It"]:
        send, wait, _ = _scripted_transport([
            "pakt-world/paktsuite-v2",
            "Endpoint returns 200",
            approval,
        ])
        interviewer = SlackInterviewer(
            send_message=send,
            wait_for_response=wait,
            allowed_orgs=("pakt-world",),
        )
        out = interviewer.interview("Add endpoint")
        assert out.human_approved, f"Slack approval {approval!r} not recognized"


# ── LucidInterviewer ─────────────────────────────────────────────


def test_lucid_interviewer_default_approver_is_brendan() -> None:
    send, wait, _ = _scripted_transport([
        "pakt-world/paktsuite-v2",
        "Endpoint returns 200",
        "yes",
    ])
    interviewer = LucidInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
    )
    out = interviewer.interview("Add /healthz endpoint")
    assert out.human_approver_id == "brendan"


def test_lucid_interviewer_uses_markdown_header() -> None:
    send, wait, sent = _scripted_transport([
        "pakt-world/paktsuite-v2",
        "Endpoint returns 200",
        "yes",
    ])
    interviewer = LucidInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
    )
    interviewer.interview("Add /healthz endpoint")
    assert any("**🐺 Coywolf — clarifying intent**" in m for m in sent)


# ── WebInterviewer ───────────────────────────────────────────────


def test_web_interviewer_recognizes_button_click_approval() -> None:
    """Frontend posts literal 'APPROVE' on button click — must register as approval."""
    send, wait, _ = _scripted_transport([
        "pakt-world/paktsuite-v2",
        "Endpoint returns 200",
        "APPROVE",  # button click
    ])
    interviewer = WebInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
    )
    out = interviewer.interview("Add endpoint")
    assert out.human_approved


def test_web_interviewer_uses_team_voice_header() -> None:
    """2touch is team-facing — drop the wolf and use a buttoned-up header."""
    send, wait, sent = _scripted_transport([
        "pakt-world/paktsuite-v2",
        "Endpoint returns 200",
        "approve",
    ])
    interviewer = WebInterviewer(
        send_message=send,
        wait_for_response=wait,
        allowed_orgs=("pakt-world",),
        approver_id="lucid-user-42",
    )
    interviewer.interview("Add endpoint")
    assert any("**Coywolf — confirming what you'd like to ship**" in m for m in sent)
    # No wolf emoji on team-facing surface
    assert not any("🐺" in m for m in sent)
