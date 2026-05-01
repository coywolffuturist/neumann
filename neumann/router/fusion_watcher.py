"""Pre-merge QA gate — external watcher route.

Polls Fusion's daemon for tasks in the In Review column. For each task,
loads its PROMPT.md, runs the QAExecutor (Opus 4.7 via pi-claude-cli),
applies the retry policy, and dispatches the verdict back to Fusion +
WhatsApp on escalation.

External-watcher route was Brendan's lean per the spec — keeps Fusion
internals untouched and survives Fusion upgrades. Trade-off: ordering
guarantees are coarser than a Fusion patch would give, so the watcher
treats every poll cycle as idempotent.

Run as a launchd cron on the Mac Mini (every ~30s) or a long-lived
``--loop``. Production install instructions in the project README; the
plist itself ships from ``coywolffuturist/coywolf:services/`` so all
launchd assets live in one place.

CLI:
    python -m neumann.router.fusion_watcher --once
    python -m neumann.router.fusion_watcher --loop --interval 30
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .qa_executor import (
    ClaudeCliReviewer,
    QAExecutor,
    QAResult,
    QATask,
    QAReviewer,
)
from .qa_retry import RetryAction, RetryPolicy, load_policy

log = logging.getLogger("neumann.qa_watcher")


# ── Fusion task contract ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FusionTask:
    """The minimum Fusion task fields the watcher needs.

    Mapped from Fusion's daemon API response; the rest of Fusion's task
    payload is intentionally ignored. Keeping this small means Fusion
    schema changes outside these fields don't break the watcher.
    """

    id: str
    column: str
    prompt_md: str
    worktree_path: str = ""
    qa_retry_count: int = 0
    extra: dict = field(default_factory=dict)


class FusionClient(Protocol):
    """Abstraction over Fusion's daemon API. Tests mock this; production
    uses ``HttpFusionClient``.
    """

    def list_in_review(self) -> list[FusionTask]: ...
    def move_to_done(self, task_id: str, *, summary: str) -> None: ...
    def move_to_in_progress(self, task_id: str, *, error_context: str, retry_count: int) -> None: ...
    def pause(self, task_id: str, *, reason: str) -> None: ...


class WhatsAppNotifier(Protocol):
    def notify(self, message: str) -> None: ...


# ── concrete implementations ────────────────────────────────────────────────


@dataclass(frozen=True)
class HttpFusionClient:
    """Default Fusion daemon client. Talks to the local Fusion daemon over HTTP.

    Endpoint surface (consumed only — read-only contract):
      GET  /api/tasks?column=in-review        -> list[task]
      POST /api/tasks/{id}/move                {column, summary?}
      POST /api/tasks/{id}/pause               {reason}
      PATCH /api/tasks/{id}/extra              {qa_retry_count, ...}

    The Mac Mini's Fusion daemon binds at ``http://localhost:7878`` per
    ``reference_fusion_notifier.md`` and watchdog plumbing. Override
    ``base_url`` for tests / staging.
    """

    base_url: str = "http://localhost:7878"
    timeout_s: int = 10

    def list_in_review(self) -> list[FusionTask]:
        data = self._get("/api/tasks?column=in-review")
        out: list[FusionTask] = []
        for item in data if isinstance(data, list) else []:
            try:
                extra = item.get("extra") or {}
                out.append(
                    FusionTask(
                        id=str(item["id"]),
                        column=str(item.get("column", "in-review")),
                        prompt_md=str(item.get("prompt_md", "")),
                        worktree_path=str(item.get("worktree_path", "")),
                        qa_retry_count=int(extra.get("qa_retry_count", 0)),
                        extra=extra,
                    )
                )
            except (KeyError, TypeError, ValueError) as e:
                log.warning("skipping malformed task payload: %s", e)
        return out

    def move_to_done(self, task_id: str, *, summary: str) -> None:
        self._post(f"/api/tasks/{task_id}/move", {"column": "done", "summary": summary})

    def move_to_in_progress(
        self, task_id: str, *, error_context: str, retry_count: int
    ) -> None:
        self._post(
            f"/api/tasks/{task_id}/move",
            {
                "column": "in-progress",
                "error_context": error_context,
                "extra": {"qa_retry_count": retry_count},
            },
        )

    def pause(self, task_id: str, *, reason: str) -> None:
        self._post(f"/api/tasks/{task_id}/pause", {"reason": reason})

    # ── private ───────────────────────────────────────────────

    def _get(self, path: str) -> object:
        req = urllib.request.Request(self.base_url + path, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict) -> object:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            body = resp.read().decode("utf-8") or "{}"
            return json.loads(body)


@dataclass(frozen=True)
class ClawdbotWhatsAppNotifier:
    """Production WhatsApp notifier — shells to ``clawdbot`` CLI.

    See ``reference_clawdbot_send.md`` — Brendan's WhatsApp is +13109483734.
    Tests don't use this class; they pass a stub.
    """

    cli_path: str = "clawdbot"
    target: str = "+13109483734"

    def notify(self, message: str) -> None:
        cli = shutil.which(self.cli_path) or self.cli_path
        cmd = [
            cli,
            "message", "send",
            "--target", self.target,
            "--channel", "whatsapp",
            "--message", message,
        ]
        try:
            subprocess.run(cmd, check=True, timeout=15, capture_output=True, text=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # Notification failure must NOT crash the watcher — log + drop.
            log.error("WhatsApp notify failed: %s", e)


# ── watcher ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WatcherStats:
    seen: int = 0
    passed: int = 0
    retried: int = 0
    escalated: int = 0
    skipped: int = 0
    planner_bugs: int = 0


class FusionWatcher:
    """Owns one polling cycle. Stateless across cycles — every cycle
    re-reads from Fusion, so missed events recover on the next tick.
    """

    def __init__(
        self,
        *,
        fusion: FusionClient,
        executor: QAExecutor,
        notifier: WhatsAppNotifier,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._fusion = fusion
        self._executor = executor
        self._notifier = notifier
        self._policy = policy or load_policy()

    def tick(self) -> WatcherStats:
        """Run one poll cycle. Returns stats for observability."""
        seen = passed = retried = escalated = skipped = planner_bugs = 0
        for ftask in self._fusion.list_in_review():
            seen += 1
            qa_task = QATask(
                task_id=ftask.id,
                prompt_md=ftask.prompt_md,
                worktree_path=ftask.worktree_path,
                context={"column": ftask.column},
            )
            result = self._executor.execute(qa_task)
            attempt_num = ftask.qa_retry_count + 1
            action = self._policy.decide(verdict=result.verdict, attempt=attempt_num)
            self._dispatch(ftask, result, action)

            if result.is_planner_bug:
                planner_bugs += 1
            if action == RetryAction.DONE:
                if result.verdict.upper() == "SKIP":
                    skipped += 1
                else:
                    passed += 1
            elif action == RetryAction.RETRY:
                retried += 1
            else:
                escalated += 1

        return WatcherStats(
            seen=seen,
            passed=passed,
            retried=retried,
            escalated=escalated,
            skipped=skipped,
            planner_bugs=planner_bugs,
        )

    # ── private ───────────────────────────────────────────────

    def _dispatch(
        self, ftask: FusionTask, result: QAResult, action: RetryAction
    ) -> None:
        if action == RetryAction.DONE:
            self._fusion.move_to_done(
                ftask.id,
                summary=result.summary or f"QA verdict={result.verdict}",
            )
            return

        if action == RetryAction.RETRY:
            self._fusion.move_to_in_progress(
                ftask.id,
                error_context=self._format_failure_context(result),
                retry_count=ftask.qa_retry_count + 1,
            )
            return

        # PAUSE_ESCALATE
        reason = self._format_pause_reason(ftask, result)
        self._fusion.pause(ftask.id, reason=reason)
        self._notifier.notify(self._format_whatsapp_message(ftask, result))

    @staticmethod
    def _format_failure_context(result: QAResult) -> str:
        steps_summary = "\n".join(
            f"  step {s.n} [{s.result}] {s.action} → {s.observed}"
            for s in result.steps
            if s.result.upper() == "FAIL"
        )
        return (
            f"QA verdict: {result.verdict}\n"
            f"Summary: {result.summary}\n"
            f"Failed steps:\n{steps_summary or '  (none reported)'}\n"
            f"Reproducible context:\n{result.reproducible_context or '(none)'}"
        )

    @staticmethod
    def _format_pause_reason(ftask: FusionTask, result: QAResult) -> str:
        if result.is_planner_bug:
            return (
                f"PLANNER_BUG (pre-merge QA): {result.summary}. "
                "Planner bug — re-running won't help; the QA Test must be fixed."
            )
        return (
            f"QA gate exhausted retries ({ftask.qa_retry_count + 1} attempts). "
            f"Last verdict={result.verdict}. Last summary: {result.summary}"
        )

    @staticmethod
    def _format_whatsapp_message(ftask: FusionTask, result: QAResult) -> str:
        head = (
            f"🐺 QA escalation: task {ftask.id} paused "
            f"(verdict={result.verdict}, attempts={ftask.qa_retry_count + 1})."
        )
        return f"{head} Reason: {result.summary[:300]}"


# ── CLI ─────────────────────────────────────────────────────────────────────


def _build_default_watcher() -> FusionWatcher:
    return FusionWatcher(
        fusion=HttpFusionClient(),
        executor=QAExecutor(reviewer=ClaudeCliReviewer()),
        notifier=ClawdbotWhatsAppNotifier(),
        policy=load_policy(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="neumann-qa-watcher")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument("--loop", action="store_true", help="run indefinitely")
    parser.add_argument("--interval", type=float, default=30.0, help="loop sleep seconds")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    if not (args.once or args.loop):
        parser.error("specify --once or --loop")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    watcher = _build_default_watcher()

    if args.once:
        stats = watcher.tick()
        log.info("tick stats: %s", stats)
        return 0

    while True:
        try:
            stats = watcher.tick()
            log.info("tick stats: %s", stats)
        except Exception:  # noqa: BLE001 — daemon must not die on transient errors
            log.exception("tick failed; continuing")
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
