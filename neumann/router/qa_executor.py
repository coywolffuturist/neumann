"""Pre-merge QA executor — runs a single QA Test against a worktree.

Orchestration only. Does not classify, does not route, does not run browsers.
Inputs: a ``QATask`` (task_id, prompt_md, worktree_path) plus a ``QAReviewer``
that knows how to invoke the underlying LLM (Opus 4.7 via pi-claude-cli in
production; mocked in tests).

Output: a ``QAResult`` with verdict, per-step results, summary,
reproducible_context, and any planner-bug detail. Verdicts are the same five
the QA persona's system prompt commits to: PASS / FAIL / SKIP / PLANNER_BUG.

The executor does NOT decide retry/escalation — that's ``qa_retry``'s job.
The executor reports facts; the watcher applies policy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .qa_test import (
    QATest,
    QATestParseError,
    parse_qa_test,
)


@dataclass(frozen=True)
class QATask:
    """Input to the QA executor — a Fusion task entering In Review."""

    task_id: str
    prompt_md: str
    worktree_path: str = ""
    # Free-form context the watcher captures from Fusion (column entry timestamp,
    # branch name, etc.). Threaded through to the reviewer prompt for traceability.
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class QAStepResult:
    n: int
    action: str
    observed: str
    result: str  # PASS | FAIL | N/A


@dataclass(frozen=True)
class QAResult:
    verdict: str  # PASS | FAIL | SKIP | PLANNER_BUG
    task_id: str
    reviewer_tier: str
    steps: tuple[QAStepResult, ...]
    failed_steps: tuple[int, ...]
    matched_expected_failure: str | None
    summary: str
    reproducible_context: str
    raw_reviewer_output: str = ""  # for debugging / log capture

    @property
    def passed(self) -> bool:
        return self.verdict.upper() == "PASS"

    @property
    def is_planner_bug(self) -> bool:
        return self.verdict.upper() == "PLANNER_BUG"


class QAReviewer(Protocol):
    """The LLM-backed reviewer. Production impl shells out to pi-claude-cli;
    tests provide a stub that returns a canned JSON verdict.
    """

    def review(self, *, qa_test: QATest, qa_task: QATask) -> str:
        """Return the raw JSON string produced by the reviewer LLM.

        The string must match the schema in ``qa-system-prompt.md`` (verdict,
        steps, etc.). The executor parses and validates the JSON.
        """
        ...


class QAExecutor:
    """Runs one QA Test attempt. Stateless — instantiate per attempt."""

    def __init__(self, reviewer: QAReviewer) -> None:
        self._reviewer = reviewer

    def execute(self, task: QATask) -> QAResult:
        # 1. Parse PROMPT.md. Parse failures are PLANNER_BUG, never FAIL —
        #    retrying won't help; the planner has to fix the spec.
        try:
            qa_test = parse_qa_test(task.prompt_md)
        except QATestParseError as e:
            return _planner_bug_result(task, reason=str(e))

        # 2. Tier check. Pre-merge executor only runs pre-merge / both.
        if not qa_test.runs_pre_merge:
            return QAResult(
                verdict="SKIP",
                task_id=task.task_id,
                reviewer_tier=qa_test.reviewer_tier.value,
                steps=(),
                failed_steps=(),
                matched_expected_failure=None,
                summary=f"reviewer_tier={qa_test.reviewer_tier.value} → not pre-merge scope",
                reproducible_context="",
            )

        # 3. Invoke reviewer. The reviewer is responsible for actually executing
        #    the steps (against agent-browser, the worktree, etc.) and returning
        #    a structured JSON verdict.
        raw = self._reviewer.review(qa_test=qa_test, qa_task=task)

        # 4. Parse reviewer output. Bad output is itself a kind of planner /
        #    reviewer bug — escalate, don't retry-as-FAIL.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return _planner_bug_result(
                task,
                reason=f"Reviewer returned non-JSON output: {e}",
                raw=raw,
            )

        verdict = str(data.get("verdict", "")).upper()
        if verdict not in ("PASS", "FAIL", "SKIP", "PLANNER_BUG"):
            return _planner_bug_result(
                task,
                reason=f"Reviewer returned invalid verdict: {data.get('verdict')!r}",
                raw=raw,
            )

        steps = _parse_steps(data.get("steps", []))
        failed_steps = tuple(int(n) for n in data.get("failed_steps", []) if isinstance(n, int))

        return QAResult(
            verdict=verdict,
            task_id=task.task_id,
            reviewer_tier=qa_test.reviewer_tier.value,
            steps=steps,
            failed_steps=failed_steps,
            matched_expected_failure=data.get("matched_expected_failure"),
            summary=str(data.get("summary", "")),
            reproducible_context=str(data.get("reproducible_context", "")),
            raw_reviewer_output=raw,
        )


# ── helpers ─────────────────────────────────────────────────────────────────


def _planner_bug_result(task: QATask, *, reason: str, raw: str = "") -> QAResult:
    return QAResult(
        verdict="PLANNER_BUG",
        task_id=task.task_id,
        reviewer_tier="",
        steps=(),
        failed_steps=(),
        matched_expected_failure=None,
        summary=reason,
        reproducible_context="",
        raw_reviewer_output=raw,
    )


def _parse_steps(items: list) -> tuple[QAStepResult, ...]:
    out: list[QAStepResult] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            out.append(
                QAStepResult(
                    n=int(it.get("n", 0)),
                    action=str(it.get("action", "")),
                    observed=str(it.get("observed", "")),
                    result=str(it.get("result", "")),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(out)


# ── default production reviewer ─────────────────────────────────────────────


@dataclass(frozen=True)
class ClaudeCliReviewer:
    """Production QAReviewer that shells out to pi-claude-cli (Mac Mini's
    Claude Max wrapper — see ``reference_mac_mini_claude_auth.md``).

    Lazy import of subprocess so the module is importable in environments
    that don't have the CLI installed (e.g. the laptop). Tests don't use
    this class — they pass a stub directly.
    """

    cli_path: str = "/opt/homebrew/bin/pi-claude-cli"
    model: str = "claude-opus-4-7"
    system_prompt_path: str = str(
        Path(__file__).parent / "personas" / "qa-system-prompt.md"
    )
    timeout_s: int = 600

    def review(self, *, qa_test: QATest, qa_task: QATask) -> str:
        import subprocess

        with open(self.system_prompt_path) as f:
            system_prompt = f.read()

        user_prompt = self._render_user_prompt(qa_test=qa_test, qa_task=qa_task)
        cmd = [
            self.cli_path,
            "-p",
            "--model", self.model,
            "--append-system-prompt", system_prompt,
        ]
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            cwd=qa_task.worktree_path or None,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pi-claude-cli exit {result.returncode}: {result.stderr[:500]}"
            )
        return result.stdout.strip()

    @staticmethod
    def _render_user_prompt(*, qa_test: QATest, qa_task: QATask) -> str:
        # Re-render the QATest as a structured execution brief for the reviewer.
        # The system prompt already contains the protocol; the user prompt is
        # the concrete instance to execute.
        steps_md = "\n".join(f"{i+1}. {s}" for i, s in enumerate(qa_test.steps))
        expected_md = "\n".join(f"- {f}" for f in qa_test.expected_failures) or "(none)"
        return (
            f"Task ID: {qa_task.task_id}\n"
            f"Worktree: {qa_task.worktree_path}\n"
            f"Reviewer tier: {qa_test.reviewer_tier.value}\n"
            f"Type: {qa_test.type.value}\n"
            f"Browser tool: {qa_test.browser_tool or 'n/a'}\n"
            f"Pass criterion: {qa_test.pass_criterion}\n\n"
            f"Steps to execute:\n{steps_md}\n\n"
            f"Expected failure modes:\n{expected_md}\n\n"
            "Execute each step in order. Return a single JSON object matching "
            "the schema in your system prompt. No prose around it."
        )
