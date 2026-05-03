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

DEFAULT_FUSION_SETTINGS = Path.home() / ".fusion" / "settings.json"


def _read_fusion_base_url(settings_path: Path | str = DEFAULT_FUSION_SETTINGS) -> str:
    """Build the Fusion daemon URL from ~/.fusion/settings.json.

    The daemon's bind address (``daemonHost``/``daemonPort`` in
    settings.json) is the single source of truth. Following it means a
    rebind (e.g. from 127.0.0.1 to a Tailscale IP for cross-device
    access) doesn't require code edits in every client. Falls back to
    ``http://127.0.0.1:4040`` only when the file is unreadable, which
    matches Fusion's own default.
    """
    try:
        with open(Path(settings_path)) as f:
            s = json.load(f)
        host = s.get("daemonHost") or "127.0.0.1"
        port = s.get("daemonPort") or 4040
        return f"http://{host}:{port}"
    except (OSError, json.JSONDecodeError):
        return "http://127.0.0.1:4040"


DEFAULT_FUSION_BASE_URL = _read_fusion_base_url()
COMMENT_AUTHOR = "neumann-qa-watcher"
COMMENT_MAX = 2000


# ── Fusion task contract ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FusionTask:
    """The minimum Fusion task fields the watcher needs.

    Mapped from Fusion's daemon API response. Retry count is intentionally
    NOT a field here — Fusion has no ``task.extra``, so retry tracking lives
    in ``WatcherState``, looked up by task id at dispatch time.

    ``project_id`` carries the Fusion project scope the task was found in
    (``None`` for the unscoped/default store). Mutations MUST be issued
    against the same scope or the daemon answers ENOENT trying to read the
    task from the wrong project's ``.fusion/tasks/`` directory.
    """

    id: str
    column: str
    prompt_md: str
    worktree_path: str = ""
    project_id: str | None = None


class FusionClient(Protocol):
    """Abstraction over Fusion's daemon API. Tests mock this; production
    uses ``HttpFusionClient``. Each method maps 1:1 to a single HTTP call.

    ``project_id`` on each mutation matches the scope the task was listed
    from. Pass ``None`` for tasks in the unscoped/default store. Tests
    that don't care can default to ``None``.
    """

    def list_in_review(self) -> list[FusionTask]: ...
    def move_to_done(self, task_id: str, *, project_id: str | None = None) -> None: ...
    def move_to_in_progress(self, task_id: str, *, project_id: str | None = None) -> None: ...
    def pause(self, task_id: str, *, project_id: str | None = None) -> None: ...
    def add_comment(
        self,
        task_id: str,
        *,
        text: str,
        author: str = COMMENT_AUTHOR,
        project_id: str | None = None,
    ) -> None: ...


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
        # Fusion scopes /api/tasks per project. Without a ?projectId= the
        # endpoint returns only the unscoped/default store, so any task
        # that lives inside a named project (e.g. lucid, neumann) is
        # invisible. Iterate all known projects + the default scope so
        # the QA gate covers the whole installation.
        out: list[FusionTask] = []
        seen_ids: set[str] = set()
        scopes: list[str | None] = [None] + self._list_project_ids()
        for project_id in scopes:
            try:
                slim = self._get("/api/tasks", project_id=project_id)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                log.warning("list /api/tasks for project=%s failed: %s", project_id, e)
                continue
            if not isinstance(slim, list):
                continue
            for item in slim:
                try:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("column", "")) != "in-review":
                        continue
                    task_id = str(item["id"])
                    if task_id in seen_ids:
                        continue
                    seen_ids.add(task_id)
                    detail = self._get(f"/api/tasks/{task_id}", project_id=project_id)
                    if not isinstance(detail, dict):
                        log.warning("task %s detail not a dict; skipping", task_id)
                        continue
                    out.append(FusionTask(
                        id=task_id,
                        column=str(detail.get("column", "in-review")),
                        prompt_md=str(detail.get("prompt", "")),
                        worktree_path=str(detail.get("worktreePath", "")),
                        project_id=project_id,
                    ))
                except (KeyError, TypeError, ValueError) as e:
                    log.warning("skipping malformed task payload: %s", e)
        return out

    def _list_project_ids(self) -> list[str]:
        """Return Fusion project IDs. Empty list on failure (caller still
        falls back to the unscoped default store)."""
        try:
            raw = self._get("/api/projects")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            log.warning("GET /api/projects failed: %s", e)
            return []
        if not isinstance(raw, list):
            return []
        return [str(p["id"]) for p in raw if isinstance(p, dict) and p.get("id")]

    def move_to_done(self, task_id: str, *, project_id: str | None = None) -> None:
        self._post(f"/api/tasks/{task_id}/move", {"column": "done"}, project_id=project_id)

    def move_to_in_progress(self, task_id: str, *, project_id: str | None = None) -> None:
        self._post(f"/api/tasks/{task_id}/move", {"column": "in-progress"}, project_id=project_id)

    def pause(self, task_id: str, *, project_id: str | None = None) -> None:
        self._post(f"/api/tasks/{task_id}/pause", {}, project_id=project_id)

    def add_comment(
        self,
        task_id: str,
        *,
        text: str,
        author: str = COMMENT_AUTHOR,
        project_id: str | None = None,
    ) -> None:
        # Fusion enforces 1..2000 chars; truncate defensively so a chatty
        # failure context doesn't 400 us into a no-op.
        truncated = text[:COMMENT_MAX]
        self._post(
            f"/api/tasks/{task_id}/comments",
            {"text": truncated, "author": author},
            project_id=project_id,
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

    def _get(self, path: str, *, project_id: str | None = None) -> object:
        if project_id:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}projectId={project_id}"
        req = urllib.request.Request(
            self.base_url + path, method="GET", headers=self._headers(),
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict, *, project_id: str | None = None) -> object:
        if project_id:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}projectId={project_id}"
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
class DryRunFusionClient:
    """Read-only Fusion client. Wraps a real client for ``list_in_review``
    so the smoke test sees real tasks; logs (instead of executing) every
    mutation. Used by ``--dry-run`` so deploy verification can't move
    tasks, post comments, or pause anything.
    """

    inner: FusionClient

    def list_in_review(self) -> list[FusionTask]:
        return self.inner.list_in_review()

    def move_to_done(self, task_id: str, *, project_id: str | None = None) -> None:
        log.info("[dry-run] would move_to_done task=%s project=%s", task_id, project_id)

    def move_to_in_progress(self, task_id: str, *, project_id: str | None = None) -> None:
        log.info("[dry-run] would move_to_in_progress task=%s project=%s", task_id, project_id)

    def pause(self, task_id: str, *, project_id: str | None = None) -> None:
        log.info("[dry-run] would pause task=%s project=%s", task_id, project_id)

    def add_comment(
        self,
        task_id: str,
        *,
        text: str,
        author: str = COMMENT_AUTHOR,
        project_id: str | None = None,
    ) -> None:
        log.info(
            "[dry-run] would add_comment task=%s project=%s author=%s text=%s",
            task_id, project_id, author, text[:200].replace("\n", " | "),
        )


@dataclass(frozen=True)
class DryRunNotifier:
    """Logs notifications instead of sending. WhatsApp stays quiet."""

    def notify(self, message: str) -> None:
        log.info("[dry-run] would notify: %s", message)


@dataclass(frozen=True)
class DryRunWatcherState:
    """Read-real, write-noop state. Lets dry-run still consult real
    retry counts (so the dispatch decision matches what production
    would do) without persisting any new state."""

    inner: WatcherState

    def attempts(self, task_id: str) -> int:
        return self.inner.attempts(task_id)

    def is_paused(self, task_id: str) -> bool:
        return self.inner.is_paused(task_id)

    def record(self, rec: WatcherRecord) -> None:
        log.info(
            "[dry-run] would record task=%s attempts=%d verdict=%s paused=%s",
            rec.task_id, rec.attempts, rec.last_verdict, rec.paused,
        )

    def clear(self, task_id: str) -> None:
        log.info("[dry-run] would clear task=%s", task_id)


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

        pid = ftask.project_id

        if action == RetryAction.DONE:
            self._fusion.move_to_done(ftask.id, project_id=pid)
            # PASS is silent; don't spam comments. SKIP gets a tiny breadcrumb
            # so a human grepping the task log knows the watcher saw it.
            if result.verdict.upper() == "SKIP":
                self._fusion.add_comment(
                    ftask.id, text=f"QA SKIP — {result.summary}", project_id=pid,
                )
            self._state.record(record)
            return

        if action == RetryAction.RETRY:
            self._fusion.add_comment(
                ftask.id, text=self._format_failure_context(result, attempts), project_id=pid,
            )
            self._fusion.move_to_in_progress(ftask.id, project_id=pid)
            self._state.record(record)
            return

        # PAUSE_ESCALATE
        self._fusion.add_comment(
            ftask.id, text=self._format_pause_reason(result, attempts), project_id=pid,
        )
        self._fusion.pause(ftask.id, project_id=pid)
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


def _build_default_watcher(*, dry_run: bool = False) -> FusionWatcher:
    real_fusion: FusionClient = HttpFusionClient()
    real_state = WatcherState()
    if dry_run:
        return FusionWatcher(
            fusion=DryRunFusionClient(inner=real_fusion),
            executor=QAExecutor(reviewer=ClaudeCliReviewer()),
            notifier=DryRunNotifier(),
            state=DryRunWatcherState(inner=real_state),
            policy=load_policy(),
        )
    return FusionWatcher(
        fusion=real_fusion,
        executor=QAExecutor(reviewer=ClaudeCliReviewer()),
        notifier=ClawdbotWhatsAppNotifier(),
        state=real_state,
        policy=load_policy(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="neumann-qa-watcher")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument("--loop", action="store_true", help="run indefinitely")
    parser.add_argument("--interval", type=float, default=30.0, help="loop sleep seconds")
    parser.add_argument("--dry-run", action="store_true",
                        help="log mutations + notifications instead of executing them; "
                             "leaves Fusion + state file untouched. Use for first-run "
                             "smoke tests on the Mac Mini.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    if not (args.once or args.loop):
        parser.error("specify --once or --loop")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.dry_run:
        log.info("DRY-RUN: no Fusion mutations, no WhatsApp, no state writes")
    watcher = _build_default_watcher(dry_run=args.dry_run)

    if args.once:
        # Same posture as --loop: a transient Fusion daemon outage (e.g.
        # mid-restart) must not produce a non-zero exit + traceback in
        # err.log. launchd will re-fire on the next StartInterval.
        try:
            stats = watcher.tick()
            log.info("tick stats: %s", stats)
        except Exception:  # noqa: BLE001 — daemon must not die on transient errors
            log.exception("tick failed; will retry on next launchd interval")
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
