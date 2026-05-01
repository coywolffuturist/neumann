"""Pre-merge QA gate — external watcher route.

Polls Fusion's daemon for tasks in the In Review column. For each task,
loads the task's prompt (markdown body containing ``## QA Test``), runs
the QAExecutor (Opus 4.7 via pi-claude-cli), applies the retry policy,
and dispatches the verdict back to Fusion + WhatsApp on escalation.

External-watcher route was Brendan's lean per the spec — keeps Fusion
internals untouched and survives Fusion upgrades. Trade-off: ordering
guarantees are coarser than a Fusion patch would give, so the watcher
treats every poll cycle as idempotent and persists retry counts in
its own state file (``WatcherState``) since Fusion's task object
exposes no ``extra`` field for arbitrary custom data.

Fusion API contract: see ``reference_fusion_daemon_api.md``.
- Auth: Bearer token from ``~/.fusion/settings.json`` ``daemonToken``.
- Listing: GET /api/tasks returns slim tasks (no ``prompt`` field).
  Filter ``column == "in-review"`` client-side, then GET /api/tasks/:id
  to fetch the full prompt.
- Mutations: POST /move {column}, POST /pause, POST /comments {text,author}.

CLI:
    python -m neumann.router.fusion_watcher --once
    python -m neumann.router.fusion_watcher --loop --interval 30
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

from .qa_executor import (
    ClaudeCliReviewer,
    QAExecutor,
    QAResult,
    QATask,
    QAReviewer,
)
from .qa_retry import RetryAction, RetryPolicy, load_policy
from .qa_state import WatcherRecord, WatcherState

log = logging.getLogger("neumann.qa_watcher")

DEFAULT_FUSION_BASE_URL = "http://127.0.0.1:4040"
DEFAULT_FUSION_SETTINGS = Path.home() / ".fusion" / "settings.json"
COMMENT_AUTHOR = "neumann-qa-watcher"
COMMENT_MAX = 2000


# ── Fusion task contract ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FusionTask:
    """The minimum Fusion task fields the watcher needs.

    Mapped from Fusion's daemon API response. Retry count is intentionally
    NOT a field here — Fusion has no ``task.extra``, so retry tracking lives
    in ``WatcherState``, looked up by task id at dispatch time.
    """

    id: str
    column: str
    prompt_md: str
    worktree_path: str = ""


class FusionClient(Protocol):
    """Abstraction over Fusion's daemon API. Tests mock this; production
    uses ``HttpFusionClient``. Each method maps 1:1 to a single HTTP call.
    """

    def list_in_review(self) -> list[FusionTask]: ...
    def move_to_done(self, task_id: str) -> None: ...
    def move_to_in_progress(self, task_id: str) -> None: ...
    def pause(self, task_id: str) -> None: ...
    def add_comment(self, task_id: str, *, text: str, author: str = COMMENT_AUTHOR) -> None: ...


class WhatsAppNotifier(Protocol):
    def notify(self, message: str) -> None: ...


# ── concrete implementations ────────────────────────────────────────────────


@dataclass
class HttpFusionClient:
    """Default Fusion daemon client. Real wire format per
    ``reference_fusion_daemon_api.md``. Bearer-auth via the ``daemonToken``
    in ``~/.fusion/settings.json`` (mode 0600).

    Two-step list flow: ``GET /api/tasks`` returns slim tasks with no
    ``prompt`` field, so for each in-review task we make a second call to
    ``GET /api/tasks/:id`` to pull the prompt body. List size on a healthy
    Fusion instance is ~tens of tasks, so the extra round-trips are cheap.
    """

    base_url: str = DEFAULT_FUSION_BASE_URL
    settings_path: Path | str = DEFAULT_FUSION_SETTINGS
    timeout_s: int = 10
    _token: str | None = None  # cached after first read

    # ── public ───────────────────────────────────────────────

    def list_in_review(self) -> list[FusionTask]:
        slim = self._get("/api/tasks")
        if not isinstance(slim, list):
            return []
        out: list[FusionTask] = []
        for item in slim:
            try:
                if not isinstance(item, dict):
                    continue
                if str(item.get("column", "")) != "in-review":
                    continue
                task_id = str(item["id"])
                detail = self._get(f"/api/tasks/{task_id}")
                if not isinstance(detail, dict):
                    log.warning("task %s detail not a dict; skipping", task_id)
                    continue
                out.append(FusionTask(
                    id=task_id,
                    column=str(detail.get("column", "in-review")),
                    prompt_md=str(detail.get("prompt", "")),
                    worktree_path=str(detail.get("worktreePath", "")),
                ))
            except (KeyError, TypeError, ValueError) as e:
                log.warning("skipping malformed task payload: %s", e)
        return out

    def move_to_done(self, task_id: str) -> None:
        self._post(f"/api/tasks/{task_id}/move", {"column": "done"})

    def move_to_in_progress(self, task_id: str) -> None:
        self._post(f"/api/tasks/{task_id}/move", {"column": "in-progress"})

    def pause(self, task_id: str) -> None:
        self._post(f"/api/tasks/{task_id}/pause", {})

    def add_comment(self, task_id: str, *, text: str, author: str = COMMENT_AUTHOR) -> None:
        # Fusion enforces 1..2000 chars; truncate defensively so a chatty
        # failure context doesn't 400 us into a no-op.
        truncated = text[:COMMENT_MAX]
        self._post(
            f"/api/tasks/{task_id}/comments",
            {"text": truncated, "author": author},
        )

    # ── private ───────────────────────────────────────────────

    def _token_value(self) -> str:
        if self._token is not None:
            return self._token
        path = Path(self.settings_path)
        try:
            with open(path) as f:
                settings = json.load(f)
            tok = settings.get("daemonToken")
            if not tok:
                raise RuntimeError(
                    f"daemonToken missing from {path}. Open Fusion → Settings → Daemon to mint one."
                )
            self._token = str(tok)
            return self._token
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"could not read {path}: {e}") from e

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_value()}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> object:
        req = urllib.request.Request(
            self.base_url + path, method="GET", headers=self._headers(),
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict) -> object:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
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
    paused_skipped: int = 0  # tasks already paused-by-us that we leave alone


class FusionWatcher:
    """Owns one polling cycle. Stateless across cycles in terms of work-
    in-progress; persistent retry counts live in ``WatcherState`` so a
    watcher restart preserves the retry contract.
    """

    def __init__(
        self,
        *,
        fusion: FusionClient,
        executor: QAExecutor,
        notifier: WhatsAppNotifier,
        state: WatcherState,
        policy: RetryPolicy | None = None,
        clock: callable = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(),
    ) -> None:
        self._fusion = fusion
        self._executor = executor
        self._notifier = notifier
        self._state = state
        self._policy = policy or load_policy()
        self._clock = clock

    def tick(self) -> WatcherStats:
        seen = passed = retried = escalated = skipped = planner_bugs = paused_skip = 0
        for ftask in self._fusion.list_in_review():
            seen += 1

            # If we've already paused this task on a prior tick, skip — the
            # human (Brendan) needs to inspect and unpause/re-spec before we
            # touch it again. Otherwise we'd ping WhatsApp on every cycle.
            if self._state.is_paused(ftask.id):
                paused_skip += 1
                continue

            qa_task = QATask(
                task_id=ftask.id,
                prompt_md=ftask.prompt_md,
                worktree_path=ftask.worktree_path,
                context={"column": ftask.column},
            )
            result = self._executor.execute(qa_task)
            attempts_so_far = self._state.attempts(ftask.id) + 1
            action = self._policy.decide(verdict=result.verdict, attempt=attempts_so_far)
            self._dispatch(ftask, result, action, attempts_so_far)

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
            paused_skipped=paused_skip,
        )

    # ── private ───────────────────────────────────────────────

    def _dispatch(
        self,
        ftask: FusionTask,
        result: QAResult,
        action: RetryAction,
        attempts: int,
    ) -> None:
        record = WatcherRecord(
            task_id=ftask.id,
            attempts=attempts,
            last_verdict=result.verdict,
            last_summary=(result.summary or "")[:500],
            last_updated=self._clock(),
        )

        if action == RetryAction.DONE:
            self._fusion.move_to_done(ftask.id)
            # PASS is silent; don't spam comments. SKIP gets a tiny breadcrumb
            # so a human grepping the task log knows the watcher saw it.
            if result.verdict.upper() == "SKIP":
                self._fusion.add_comment(
                    ftask.id, text=f"QA SKIP — {result.summary}",
                )
            self._state.record(record)
            return

        if action == RetryAction.RETRY:
            self._fusion.add_comment(
                ftask.id, text=self._format_failure_context(result, attempts),
            )
            self._fusion.move_to_in_progress(ftask.id)
            self._state.record(record)
            return

        # PAUSE_ESCALATE
        self._fusion.add_comment(
            ftask.id, text=self._format_pause_reason(result, attempts),
        )
        self._fusion.pause(ftask.id)
        record.paused = True
        self._state.record(record)
        self._notifier.notify(self._format_whatsapp_message(ftask, result, attempts))

    @staticmethod
    def _format_failure_context(result: QAResult, attempts: int) -> str:
        steps_summary = "\n".join(
            f"  step {s.n} [{s.result}] {s.action} → {s.observed}"
            for s in result.steps
            if s.result.upper() == "FAIL"
        )
        return (
            f"QA verdict: {result.verdict} (attempt {attempts})\n"
            f"Summary: {result.summary}\n"
            f"Failed steps:\n{steps_summary or '  (none reported)'}\n"
            f"Reproducible context: {result.reproducible_context or '(none)'}"
        )

    @staticmethod
    def _format_pause_reason(result: QAResult, attempts: int) -> str:
        if result.is_planner_bug:
            return (
                f"QA PLANNER_BUG (pre-merge, attempt {attempts}): {result.summary}. "
                "Re-running won't help; the QA Test must be fixed by the planner."
            )
        return (
            f"QA gate exhausted retries after {attempts} attempts. "
            f"Last verdict={result.verdict}. {result.summary}"
        )

    @staticmethod
    def _format_whatsapp_message(
        ftask: FusionTask, result: QAResult, attempts: int,
    ) -> str:
        head = (
            f"🐺 QA escalation: task {ftask.id} paused "
            f"(verdict={result.verdict}, attempts={attempts})."
        )
        return f"{head} Reason: {result.summary[:300]}"


# ── CLI ─────────────────────────────────────────────────────────────────────


def _build_default_watcher() -> FusionWatcher:
    return FusionWatcher(
        fusion=HttpFusionClient(),
        executor=QAExecutor(reviewer=ClaudeCliReviewer()),
        notifier=ClawdbotWhatsAppNotifier(),
        state=WatcherState(),
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
