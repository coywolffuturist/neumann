"""Tests for FusionWatcher — the polling loop and dispatch logic.

End-to-end via mock FusionClient + stub QAReviewer + stub WhatsAppNotifier
+ tmp-path WatcherState. Exercises the full retry/escalation contract:
PASS → Done, SKIP → Done with breadcrumb comment, FAIL+retry-left → comment
+ In Progress, FAIL+retries-exhausted → comment + pause + WhatsApp,
PLANNER_BUG → comment + pause + WhatsApp (distinct message), already-paused
tasks are left alone, retry counter persists across ticks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neumann.router.fusion_watcher import (
    COMMENT_AUTHOR,
    FusionTask,
    FusionWatcher,
    WatcherStats,
)
from neumann.router.qa_executor import QAExecutor
from neumann.router.qa_retry import RetryPolicy
from neumann.router.qa_state import WatcherState


# ── fakes ──────────────────────────────────────────────────────────────────


@dataclass
class _FakeFusionClient:
    queue: list[FusionTask] = field(default_factory=list)
    moves: list[dict] = field(default_factory=list)
    pauses: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)

    def list_in_review(self) -> list[FusionTask]:
        out = self.queue
        self.queue = []
        return out

    def move_to_done(self, task_id: str) -> None:
        self.moves.append({"task_id": task_id, "column": "done"})

    def move_to_in_progress(self, task_id: str) -> None:
        self.moves.append({"task_id": task_id, "column": "in-progress"})

    def pause(self, task_id: str) -> None:
        self.pauses.append({"task_id": task_id})

    def add_comment(self, task_id: str, *, text: str, author: str = COMMENT_AUTHOR) -> None:
        self.comments.append({"task_id": task_id, "text": text, "author": author})


@dataclass
class _FakeNotifier:
    messages: list[str] = field(default_factory=list)

    def notify(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class _ScriptedReviewer:
    """Returns canned JSON verdicts in order."""

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
    state_path: Path,
    max_retries: int = 2,
):
    fusion = _FakeFusionClient(queue=queue)
    notifier = _FakeNotifier()
    reviewer = _ScriptedReviewer(scripts=scripts)
    executor = QAExecutor(reviewer=reviewer)
    state = WatcherState(path=state_path)
    watcher = FusionWatcher(
        fusion=fusion,
        executor=executor,
        notifier=notifier,
        state=state,
        policy=RetryPolicy(max_retries=max_retries),
        clock=lambda: "2026-05-01T00:00:00Z",
    )
    return watcher, fusion, notifier, reviewer, state


# ── verdict → action dispatch ──────────────────────────────────────────────


def test_pass_moves_task_to_done_silently(tmp_path: Path) -> None:
    watcher, fusion, notifier, _, state = _build_watcher(
        queue=[FusionTask(id="T-1", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("PASS", summary="all good")],
        state_path=tmp_path / "state.json",
    )
    stats = watcher.tick()
    assert stats == WatcherStats(seen=1, passed=1)
    assert fusion.moves == [{"task_id": "T-1", "column": "done"}]
    assert not fusion.pauses
    assert not fusion.comments  # PASS is silent; no comment spam
    assert not notifier.messages
    rec = state._load()["T-1"]
    assert rec["last_verdict"] == "PASS"
    assert rec["attempts"] == 1
    assert rec["paused"] is False


def test_skip_moves_to_done_with_breadcrumb_comment(tmp_path: Path) -> None:
    """SKIP (post-deploy-only) moves to Done so it doesn't sit in In Review,
    but leaves a comment explaining why so a human reading the task log
    knows the watcher saw it."""
    pre_only_post = _prompt(reviewer_tier="post-deploy").replace(
        "**Pre-merge model:** claude-opus-4-7\n", ""
    )
    watcher, fusion, _, reviewer, _ = _build_watcher(
        queue=[FusionTask(id="T-2", column="in-review", prompt_md=pre_only_post)],
        scripts=[],  # never called for SKIP
        state_path=tmp_path / "state.json",
    )
    stats = watcher.tick()
    assert stats.skipped == 1
    assert reviewer.cursor == 0
    assert fusion.moves[0]["column"] == "done"
    assert len(fusion.comments) == 1
    assert "QA SKIP" in fusion.comments[0]["text"]
    assert fusion.comments[0]["author"] == COMMENT_AUTHOR


def test_first_fail_comments_then_moves_to_in_progress(tmp_path: Path) -> None:
    watcher, fusion, notifier, _, state = _build_watcher(
        queue=[FusionTask(id="T-3", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", summary="step 2 failed")],
        state_path=tmp_path / "state.json",
    )
    stats = watcher.tick()
    assert stats == WatcherStats(seen=1, retried=1)
    assert fusion.moves == [{"task_id": "T-3", "column": "in-progress"}]
    assert len(fusion.comments) == 1
    assert "QA verdict: FAIL" in fusion.comments[0]["text"]
    assert "attempt 1" in fusion.comments[0]["text"]
    assert "step 2 failed" in fusion.comments[0]["text"]
    assert not fusion.pauses
    assert not notifier.messages
    assert state.attempts("T-3") == 1
    assert not state.is_paused("T-3")


def test_retry_count_persists_across_ticks(tmp_path: Path) -> None:
    """First tick: FAIL #1 → retry. Second tick: FAIL #2 → retry. Third:
    FAIL #3 → escalate. Retry counter must persist through fresh
    watcher instances (mimicking launchd cron firing)."""
    state_path = tmp_path / "state.json"

    # Tick 1
    watcher, fusion, notifier, _, _ = _build_watcher(
        queue=[FusionTask(id="T-4", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", summary="r1")],
        state_path=state_path,
    )
    s1 = watcher.tick()
    assert s1.retried == 1
    assert not fusion.pauses

    # Tick 2 — fresh instances, same state file
    watcher2, fusion2, notifier2, _, _ = _build_watcher(
        queue=[FusionTask(id="T-4", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", summary="r2")],
        state_path=state_path,
    )
    s2 = watcher2.tick()
    assert s2.retried == 1
    assert not fusion2.pauses

    # Tick 3 — exhaust
    watcher3, fusion3, notifier3, _, state3 = _build_watcher(
        queue=[FusionTask(id="T-4", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", summary="r3")],
        state_path=state_path,
    )
    s3 = watcher3.tick()
    assert s3.escalated == 1
    assert fusion3.pauses == [{"task_id": "T-4"}]
    assert len(notifier3.messages) == 1
    assert "attempts=3" in notifier3.messages[0]
    assert state3.is_paused("T-4")
    assert state3.attempts("T-4") == 3


def test_already_paused_task_is_left_alone(tmp_path: Path) -> None:
    """Once we've paused a task, leave it alone until a human unpauses
    or clears the watcher state — otherwise we'd re-ping WhatsApp every
    cycle."""
    state_path = tmp_path / "state.json"
    state = WatcherState(path=state_path)
    from neumann.router.qa_state import WatcherRecord
    state.record(WatcherRecord(
        task_id="T-5", attempts=3, last_verdict="FAIL",
        last_summary="exhausted", last_updated="prev", paused=True,
    ))

    watcher, fusion, notifier, reviewer, _ = _build_watcher(
        queue=[FusionTask(id="T-5", column="in-review", prompt_md=_prompt())],
        scripts=[],  # never called
        state_path=state_path,
    )
    stats = watcher.tick()
    assert stats.seen == 1
    assert stats.paused_skipped == 1
    assert reviewer.cursor == 0
    assert not fusion.moves
    assert not fusion.pauses
    assert not fusion.comments
    assert not notifier.messages


def test_planner_bug_pauses_immediately_with_distinct_message(tmp_path: Path) -> None:
    bad_prompt = "# task\n\nno QA test here\n"
    watcher, fusion, notifier, reviewer, state = _build_watcher(
        queue=[FusionTask(id="T-6", column="in-review", prompt_md=bad_prompt)],
        scripts=[],  # reviewer must NOT be called
        state_path=tmp_path / "state.json",
    )
    stats = watcher.tick()
    assert stats.escalated == 1
    assert stats.planner_bugs == 1
    assert reviewer.cursor == 0
    assert fusion.pauses == [{"task_id": "T-6"}]
    assert len(fusion.comments) == 1
    assert "PLANNER_BUG" in fusion.comments[0]["text"]
    assert "must be fixed by the planner" in fusion.comments[0]["text"]
    assert len(notifier.messages) == 1
    assert state.is_paused("T-6")


# ── multiple tasks in one tick ─────────────────────────────────────────────


def test_tick_handles_mixed_verdicts_in_one_cycle(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    # Pre-load state for B so it's already on its 2nd attempt — next FAIL will escalate.
    state = WatcherState(path=state_path)
    from neumann.router.qa_state import WatcherRecord
    state.record(WatcherRecord(task_id="B", attempts=2, last_verdict="FAIL", last_summary="r2"))
    state._cache = None  # force reload from disk in the watcher's state

    queue = [
        FusionTask(id="A", column="in-review", prompt_md=_prompt()),
        FusionTask(id="B", column="in-review", prompt_md=_prompt()),
        FusionTask(id="C", column="in-review", prompt_md=_prompt()),
    ]
    scripts = [
        _verdict("PASS", task_id="A"),
        _verdict("FAIL", task_id="B", summary="exhausted on B"),
        _verdict("FAIL", task_id="C", summary="first fail on C"),
    ]
    watcher, fusion, notifier, _, _ = _build_watcher(
        queue=queue, scripts=scripts, state_path=state_path,
    )
    stats = watcher.tick()
    assert stats == WatcherStats(seen=3, passed=1, retried=1, escalated=1)
    assert {m["task_id"] for m in fusion.moves} == {"A", "C"}
    assert [p["task_id"] for p in fusion.pauses] == ["B"]
    assert len(notifier.messages) == 1


def test_empty_queue_is_no_op(tmp_path: Path) -> None:
    watcher, fusion, notifier, reviewer, _ = _build_watcher(
        queue=[], scripts=[], state_path=tmp_path / "state.json",
    )
    stats = watcher.tick()
    assert stats == WatcherStats()
    assert not fusion.moves
    assert not fusion.pauses
    assert not fusion.comments
    assert not notifier.messages
    assert reviewer.cursor == 0


# ── retry threshold tunable ────────────────────────────────────────────────


def test_max_retries_zero_escalates_on_first_fail(tmp_path: Path) -> None:
    """``max_retries=0`` is a valid op-mode (e.g. CI: never auto-retry)."""
    watcher, fusion, notifier, _, _ = _build_watcher(
        queue=[FusionTask(id="T-Z", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", task_id="T-Z")],
        state_path=tmp_path / "state.json",
        max_retries=0,
    )
    stats = watcher.tick()
    assert stats.escalated == 1
    assert len(notifier.messages) == 1


def test_max_retries_high_keeps_retrying(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = WatcherState(path=state_path)
    from neumann.router.qa_state import WatcherRecord
    state.record(WatcherRecord(task_id="T-H", attempts=3, last_verdict="FAIL", last_summary="r3"))
    state._cache = None

    watcher, fusion, notifier, _, _ = _build_watcher(
        queue=[FusionTask(id="T-H", column="in-review", prompt_md=_prompt())],
        scripts=[_verdict("FAIL", task_id="T-H")],
        state_path=state_path, max_retries=10,
    )
    stats = watcher.tick()
    assert stats.retried == 1
    assert fusion.moves[0] == {"task_id": "T-H", "column": "in-progress"}
    assert not fusion.pauses
