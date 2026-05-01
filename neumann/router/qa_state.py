"""Watcher state for the pre-merge QA gate.

Fusion's task object has no ``extra`` field for arbitrary custom data
(verified 2026-05-01 against `@runfusion/fusion` daemon source — see
``reference_fusion_daemon_api.md``). So the QA retry counter cannot live
on the task itself; it lives in a separate atomic-write JSON file keyed
by task id.

Mirrors the StateStore pattern used by Phase 3's ``coywolf-qa`` cron;
keeping the same shape on both sides means the dashboard sub-spec
(Phase 4) reads one schema across both tiers.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_STATE_PATH = Path.home() / ".coywolf" / "state" / "neumann-qa-watcher.json"


@dataclass
class WatcherRecord:
    task_id: str
    attempts: int = 0  # number of QA attempts made so far on this task
    last_verdict: str = ""
    last_summary: str = ""
    last_updated: str = ""
    paused: bool = False  # set true when the watcher pauses + escalates


class WatcherState:
    """JSON-on-disk retry-count + last-verdict log. Atomic writes; corrupt-tolerant."""

    def __init__(self, path: Path | str = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)
        self._cache: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = {}
            return self._cache
        try:
            with open(self.path) as f:
                data = json.load(f)
            self._cache = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            self._cache = {}
        return self._cache

    # ── public ────────────────────────────────────────────────

    def attempts(self, task_id: str) -> int:
        rec = self._load().get(task_id)
        return int(rec.get("attempts", 0)) if isinstance(rec, dict) else 0

    def is_paused(self, task_id: str) -> bool:
        rec = self._load().get(task_id)
        return bool(rec.get("paused", False)) if isinstance(rec, dict) else False

    def record(self, rec: WatcherRecord) -> None:
        data = self._load()
        data[rec.task_id] = asdict(rec)
        self._atomic_write(data)

    def clear(self, task_id: str) -> None:
        """Remove the record (e.g. after a pause is manually unpaused by Brendan)."""
        data = self._load()
        if task_id in data:
            del data[task_id]
            self._atomic_write(data)

    # ── private ───────────────────────────────────────────────

    def _atomic_write(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".neumann-qa-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
