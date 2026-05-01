"""Tests for QAExecutor — the per-attempt orchestration layer."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from neumann.router.qa_executor import (
    QAExecutor,
    QAResult,
    QAStepResult,
    QATask,
)
from neumann.router.qa_test import QATest


# ── helpers ────────────────────────────────────────────────────────────────


def _good_prompt(reviewer_tier: str = "both") -> str:
    return (
        "# Task: example\n\n"
        "## QA Test\n\n"
        f"**Type:** browser\n"
        f"**Reviewer tier:** {reviewer_tier}\n"
        "**Pre-merge model:** claude-opus-4-7\n"
        "**Post-deploy model:** qwen-3.6\n"
        "**Pass criterion:** all assertions pass\n"
        "**Browser tool:** agent-browser --show\n\n"
        "### Steps\n\n"
        "1. Open `http://localhost:7777` via `agent-browser --show`.\n"
        "2. Assert: `document.title === 'Lucid'`.\n"
    )


@dataclass
class _StubReviewer:
    """Captures inputs for assertions; returns canned output."""

    canned: str
    received_qa_test: QATest | None = None
    received_qa_task: QATask | None = None

    def review(self, *, qa_test, qa_task):
        self.received_qa_test = qa_test
        self.received_qa_task = qa_task
        return self.canned


def _verdict_json(
    *,
    verdict: str,
    task_id: str = "T-1",
    summary: str = "",
    steps: list | None = None,
    failed_steps: list | None = None,
    matched: str | None = None,
    repro: str = "",
) -> str:
    return json.dumps({
        "verdict": verdict,
        "task_id": task_id,
        "reviewer_tier": "pre-merge",
        "steps": steps or [],
        "failed_steps": failed_steps or [],
        "matched_expected_failure": matched,
        "summary": summary,
        "reproducible_context": repro,
    })


# ── happy paths ────────────────────────────────────────────────────────────


def test_executor_returns_pass_on_pass_verdict() -> None:
    reviewer = _StubReviewer(canned=_verdict_json(verdict="PASS", summary="all good"))
    exec_ = QAExecutor(reviewer=reviewer)
    task = QATask(task_id="T-1", prompt_md=_good_prompt())
    result = exec_.execute(task)
    assert result.verdict == "PASS"
    assert result.passed
    assert result.summary == "all good"
    assert reviewer.received_qa_test is not None
    assert reviewer.received_qa_test.type.value == "browser"


def test_executor_returns_fail_on_fail_verdict() -> None:
    reviewer = _StubReviewer(canned=_verdict_json(
        verdict="FAIL",
        summary="step 2 failed",
        steps=[
            {"n": 1, "action": "Open page", "observed": "loaded", "result": "PASS"},
            {"n": 2, "action": "Assert title", "observed": "got 'wat'", "result": "FAIL"},
        ],
        failed_steps=[2],
    ))
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-2", prompt_md=_good_prompt()))
    assert result.verdict == "FAIL"
    assert not result.passed
    assert len(result.steps) == 2
    assert result.failed_steps == (2,)


# ── tier scoping ───────────────────────────────────────────────────────────


def test_executor_skips_when_post_deploy_only() -> None:
    """Pre-merge executor should not run post-deploy-only tests — that's Coywolf's job."""
    reviewer = _StubReviewer(canned="should not be called")
    exec_ = QAExecutor(reviewer=reviewer)
    task = QATask(
        task_id="T-3",
        prompt_md=_good_prompt(reviewer_tier="post-deploy")
        .replace("**Pre-merge model:** claude-opus-4-7\n", ""),  # not required when post-deploy-only
    )
    result = exec_.execute(task)
    assert result.verdict == "SKIP"
    assert "post-deploy" in result.summary
    # Confirm the reviewer was NOT called.
    assert reviewer.received_qa_task is None


def test_executor_runs_for_both_tier() -> None:
    reviewer = _StubReviewer(canned=_verdict_json(verdict="PASS"))
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-4", prompt_md=_good_prompt(reviewer_tier="both")))
    assert result.verdict == "PASS"


def test_executor_runs_for_pre_merge_tier() -> None:
    reviewer = _StubReviewer(canned=_verdict_json(verdict="PASS"))
    exec_ = QAExecutor(reviewer=reviewer)
    pre_only = (
        _good_prompt(reviewer_tier="pre-merge")
        .replace("**Post-deploy model:** qwen-3.6\n", "")
    )
    result = exec_.execute(QATask(task_id="T-5", prompt_md=pre_only))
    assert result.verdict == "PASS"


# ── parser-bug short-circuit ───────────────────────────────────────────────


def test_executor_returns_planner_bug_when_prompt_md_missing_qa_test() -> None:
    reviewer = _StubReviewer(canned="should not be called")
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-6", prompt_md="# Just a header\n"))
    assert result.verdict == "PLANNER_BUG"
    assert "missing a '## QA Test' section" in result.summary
    # Reviewer untouched — no LLM call wasted on un-runnable input.
    assert reviewer.received_qa_task is None


def test_executor_returns_planner_bug_on_banned_tool() -> None:
    bad = _good_prompt().replace(
        "Open `http://localhost:7777` via `agent-browser --show`.",
        "Open via Clawd Browser Relay.",
    )
    reviewer = _StubReviewer(canned="should not be called")
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-7", prompt_md=bad))
    assert result.verdict == "PLANNER_BUG"
    assert "banned tool" in result.summary
    assert reviewer.received_qa_task is None


# ── reviewer output validation ─────────────────────────────────────────────


def test_executor_treats_non_json_reviewer_output_as_planner_bug() -> None:
    reviewer = _StubReviewer(canned="this is not JSON at all")
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-8", prompt_md=_good_prompt()))
    assert result.verdict == "PLANNER_BUG"
    assert "non-JSON" in result.summary
    assert result.raw_reviewer_output == "this is not JSON at all"


def test_executor_treats_invalid_verdict_as_planner_bug() -> None:
    reviewer = _StubReviewer(canned=json.dumps({"verdict": "MAYBE"}))
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-9", prompt_md=_good_prompt()))
    assert result.verdict == "PLANNER_BUG"
    assert "invalid verdict" in result.summary


def test_executor_normalizes_lowercase_verdict() -> None:
    reviewer = _StubReviewer(canned=json.dumps({
        "verdict": "pass",
        "task_id": "T-10",
        "summary": "ok",
        "steps": [],
    }))
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-10", prompt_md=_good_prompt()))
    assert result.verdict == "PASS"


# ── step parsing ───────────────────────────────────────────────────────────


def test_executor_parses_step_results() -> None:
    reviewer = _StubReviewer(canned=_verdict_json(
        verdict="FAIL",
        steps=[
            {"n": 1, "action": "open", "observed": "ok", "result": "PASS"},
            {"n": 2, "action": "click", "observed": "no element", "result": "FAIL"},
        ],
        failed_steps=[2],
    ))
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-11", prompt_md=_good_prompt()))
    assert isinstance(result.steps[0], QAStepResult)
    assert result.steps[0].n == 1
    assert result.steps[1].result == "FAIL"


def test_executor_drops_malformed_step_entries() -> None:
    """Malformed step entries don't crash the executor."""
    reviewer = _StubReviewer(canned=json.dumps({
        "verdict": "PASS",
        "steps": [
            {"n": 1, "action": "ok", "observed": "ok", "result": "PASS"},
            "this is not a dict",
            {"n": "not-an-int", "action": "x", "observed": "x", "result": "x"},
        ],
    }))
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-12", prompt_md=_good_prompt()))
    # Only the well-formed step survives.
    assert len(result.steps) == 1
    assert result.verdict == "PASS"


# ── executor preserves task_id even on PLANNER_BUG ─────────────────────────


def test_planner_bug_preserves_task_id() -> None:
    reviewer = _StubReviewer(canned="should not run")
    exec_ = QAExecutor(reviewer=reviewer)
    result = exec_.execute(QATask(task_id="T-13", prompt_md="no qa test here"))
    assert result.task_id == "T-13"
