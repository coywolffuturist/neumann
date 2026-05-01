"""Tests for FusionWatcher — the polling loop and dispatch logic.

End-to-end via mock FusionClient + stub QAReviewer + stub WhatsAppNotifier.
The full retry/escalation contract is exercised here: PASS → Done,
FAIL+retry-left → In Progress, FAIL+retries-exhausted → pause+notify,
PLANNER_BUG → pause+notify (different message), SKIP → Done.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from neumann.router.fusion_watcher import (
    FusionTask,
    FusionWatcher,
    WatcherStats,
)
from neumann.router.qa_executor import QAExecutor
from neumann.router.qa_retry import RetryPolicy


# ── fakes ──────────────────────────────────────────────────────────────────


@dataclass
class _FakeFusionClient:
    queue: list[FusionTask] = field(default_factory=list)
    moves: list[dict] = field(default_factory=list)
    pauses: list[dict] = field(default_factory=list)

    def list_in_review(self) -> list[FusionTask]:
        out = self.queue
        # Simulate Fusion: tasks moved out of in-review aren't returned again.
        self.queue = []
        return out

    def move_to_done(self, task_id, *, summary):
        self.moves.append({"task_id": task_id, "column": "done", "summary": summary})

    def move_to_in_progress(self, task_id, *, error_context, retry_count):
        self.moves.append({
            "task_id": task_id,
            "column": "in-progress",
            "error_context": error_context,
            "retry_count": retry_count,
        })

    def pause(self, task_id, *, reason):
        self.pauses.append({"task_id": task_id, "reason": reason})


@dataclass
class _FakeNotifier:
    messages: list[str] = field(default_factory=list)

    def notify(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class _ScriptedReviewer:
    """Returns canned JSON verdicts in order. Useful for retry sequences."""

    scripts: list[str] = field(default_factory=list)
    cursor: int = 0

    def review(self, *, qa_test, qa_task) -> str:
        if self.cursor >= len(self.scripts):
            raise AssertionError("ScriptedReviewer ran out of canned outputs")
        out = self.scripts[self.cursor]
        self.cursor += 1
        return out


def _verdict(verdict: str, **extras: Any) -> str:
    payload = {
        "verdict": verdict,
        "task_id": extras.get("task_id", "T-1"),
        "reviewer_tier": "pre-merge",
        "steps": extras.get("steps", []),
        "failed_steps": extras.get("failed_steps", []),
        "matched_expected_failure": None,
        "summary": extras.get("summary", verdict.lower()),
        "reproducible_context": extras.get("repro", ""),
    }
    return json.dumps(payload)


def _prompt(reviewer_tier: str = "both") -> str:
    return (
        "## QA Test\n\n"
        f"**Type:** browser\n**Reviewer tier:** {reviewer_tier}\n"
        "**Pre-merge model:** claude-opus-4-7\n"
        "**Post-deploy model:** qwen-3.6\n"
        "**Pass criterion:** all assertions pass\n"
        "**Browser tool:** agent-browser --show\n\n"
        "### Steps\n\n1. Open page.\n2. Assert: title equals 'Lucid'.\n"
    )


def _build_watcher(
    *,
    queue: list[FusionTask],
    scripts: list[str],
    max_retries: int = 2,
):
    fusion = _FakeFusionClient(queue=queue)
    notifier = _FakeNotifier()
    reviewer = _ScriptedReviewer(scripts=scripts)
    executor = QAExecutor(reviewer=reviewer)
    watcher = FusionWatcher(
        fusion=fusion,
        executor=executor,
        notifier=notifier,
        policy=RetryPolicy(max_retries=max_retries),
    )
    return watcher, fusion, notifier, reviewer


# ── verdict → action dispatch ──────────────────────────────────────────────


def test_pass_moves_task_to_done() -> None:
    watcher, fusion, notifier, _ = _build_watcher(
        queue=[FusionTask(id="T-1", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("PASS", summary="all good")],
    )
    stats = watcher.tick()
    assert stats == WatcherStats(seen=1, passed=1)
    assert len(fusion.moves) == 1
    assert fusion.moves[0]["column"] == "done"
    assert "all good" in fusion.moves[0]["summary"]
    assert not fusion.pauses
    assert not notifier.messages


def test_skip_moves_task_to_done_and_counts_as_skipped() -> None:
    """SKIP (e.g. post-deploy-only) should move the task forward; pre-merge
    has no business holding it."""
    pre_only_post = _prompt(reviewer_tier="post-deploy").replace(
        "**Pre-merge model:** claude-opus-4-7\n", ""
    )
    watcher, fusion, _, _ = _build_watcher(
        queue=[FusionTask(id="T-2", column="in-review", prompt_md=pre_only_post)],
        scripts=[],  # reviewer should never be called for SKIP
    )
    stats = watcher.tick()
    assert stats.skipped == 1
    assert stats.passed == 0
    assert fusion.moves[0]["column"] == "done"


def test_first_fail_moves_to_in_progress_with_retry_count() -> None:
    watcher, fusion, notifier, _ = _build_watcher(
        queue=[FusionTask(id="T-3", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", summary="step 2 failed")],
    )
    stats = watcher.tick()
    assert stats == WatcherStats(seen=1, retried=1)
    assert fusion.moves[0]["column"] == "in-progress"
    assert fusion.moves[0]["retry_count"] == 1
    assert "step 2 failed" in fusion.moves[0]["error_context"]
    assert not fusion.pauses
    assert not notifier.messages


def test_second_fail_still_retries() -> None:
    watcher, fusion, notifier, _ = _build_watcher(
        queue=[FusionTask(id="T-4", column="in-review", prompt_md=_prompt(), qa_retry_count=1)],
        scripts=[_verdict("FAIL", summary="still broken")],
    )
    stats = watcher.tick()
    assert stats == WatcherStats(seen=1, retried=1)
    assert fusion.moves[0]["retry_count"] == 2
    assert not fusion.pauses


def test_third_fail_pauses_and_pings_whatsapp() -> None:
    watcher, fusion, notifier, _ = _build_watcher(
        queue=[FusionTask(id="T-5", column="in-review", prompt_md=_prompt(), qa_retry_count=2)],
        scripts=[_verdict("FAIL", summary="still broken on round 3")],
    )
    stats = watcher.tick()
    assert stats == WatcherStats(seen=1, escalated=1)
    assert not fusion.moves  # No move; task is paused.
    assert len(fusion.pauses) == 1
    assert "exhausted retries" in fusion.pauses[0]["reason"]
    assert len(notifier.messages) == 1
    assert "T-5" in notifier.messages[0]
    assert "FAIL" in notifier.messages[0]


def test_planner_bug_pauses_immediately_with_distinct_message() -> None:
    bad_prompt = "# task\n\nno QA test here\n"
    watcher, fusion, notifier, reviewer = _build_watcher(
        queue=[FusionTask(id="T-6", column="in-review", prompt_md=bad_prompt)],
        scripts=[],  # reviewer must NOT be called
    )
    stats = watcher.tick()
    assert stats.escalated == 1
    assert stats.planner_bugs == 1
    assert reviewer.cursor == 0
    assert len(fusion.pauses) == 1
    assert "PLANNER_BUG" in fusion.pauses[0]["reason"]
    assert "Planner bug" in fusion.pauses[0]["reason"]
    assert len(notifier.messages) == 1


# ── multiple tasks in one tick ─────────────────────────────────────────────


def test_tick_handles_mixed_verdicts_in_one_cycle() -> None:
    queue = [
        FusionTask(id="A", column="in-review", prompt_md=_prompt()),
        FusionTask(id="B", column="in-review", prompt_md=_prompt(), qa_retry_count=2),
        FusionTask(id="C", column="in-review", prompt_md=_prompt()),
    ]
    scripts = [
        _verdict("PASS", task_id="A"),
        _verdict("FAIL", task_id="B", summary="exhausted on B"),
        _verdict("FAIL", task_id="C", summary="first fail on C"),
    ]
    watcher, fusion, notifier, _ = _build_watcher(queue=queue, scripts=scripts)
    stats = watcher.tick()
    assert stats == WatcherStats(seen=3, passed=1, retried=1, escalated=1)
    assert {m["task_id"] for m in fusion.moves} == {"A", "C"}
    assert [p["task_id"] for p in fusion.pauses] == ["B"]
    assert len(notifier.messages) == 1


# ── empty queue ────────────────────────────────────────────────────────────


def test_empty_queue_is_no_op() -> None:
    watcher, fusion, notifier, reviewer = _build_watcher(queue=[], scripts=[])
    stats = watcher.tick()
    assert stats == WatcherStats()
    assert not fusion.moves
    assert not fusion.pauses
    assert not notifier.messages
    assert reviewer.cursor == 0


# ── retry threshold tunable ────────────────────────────────────────────────


def test_max_retries_zero_escalates_on_first_fail() -> None:
    """``max_retries=0`` is a valid op-mode (e.g. CI: never auto-retry)."""
    watcher, fusion, notifier, _ = _build_watcher(
        queue=[FusionTask(id="T-Z", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", task_id="T-Z")],
        max_retries=0,
    )
    stats = watcher.tick()
    assert stats.escalated == 1
    assert len(notifier.messages) == 1


def test_max_retries_high_keeps_retrying() -> None:
    queue = [FusionTask(id="T-H", column="in-review", prompt_md=_prompt(), qa_retry_count=3)]
    scripts = [_verdict("FAIL", task_id="T-H")]
    watcher, fusion, notifier, _ = _build_watcher(
        queue=queue, scripts=scripts, max_retries=10
    )
    stats = watcher.tick()
    assert stats.retried == 1
    assert fusion.moves[0]["retry_count"] == 4
    assert not fusion.pauses
