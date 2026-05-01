"""Tests for WatcherState — the retry-counter state file."""
from __future__ import annotations

import json
from pathlib import Path

from neumann.router.qa_state import WatcherRecord, WatcherState


def test_default_attempts_zero(tmp_path: Path) -> None:
    s = WatcherState(path=tmp_path / "missing.json")
    assert s.attempts("any") == 0
    assert not s.is_paused("any")


def test_record_and_read_attempts(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    s = WatcherState(path=p)
    s.record(WatcherRecord(task_id="T-1", attempts=2, last_verdict="FAIL"))
    # Re-read with a fresh instance to confirm persistence.
    s2 = WatcherState(path=p)
    assert s2.attempts("T-1") == 2
    assert not s2.is_paused("T-1")


def test_record_paused_flag(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    s = WatcherState(path=p)
    s.record(WatcherRecord(task_id="T-2", attempts=3, paused=True, last_verdict="FAIL"))
    assert WatcherState(path=p).is_paused("T-2")


def test_clear_removes_record(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    s = WatcherState(path=p)
    s.record(WatcherRecord(task_id="T-3", attempts=1, paused=True))
    s.clear("T-3")
    s2 = WatcherState(path=p)
    assert s2.attempts("T-3") == 0
    assert not s2.is_paused("T-3")


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "state.json"
    s = WatcherState(path=nested)
    s.record(WatcherRecord(task_id="T-4", attempts=1))
    assert nested.exists()
    data = json.loads(nested.read_text())
    assert data["T-4"]["attempts"] == 1


def test_corrupt_state_file_recovers(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text("{not json")
    s = WatcherState(path=p)
    assert s.attempts("X") == 0  # tolerated, treated as empty
    s.record(WatcherRecord(task_id="X", attempts=1))
    fresh = json.loads(p.read_text())
    assert "X" in fresh


def test_non_dict_state_file_recovers(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(json.dumps(["not", "a", "dict"]))
    s = WatcherState(path=p)
    assert s.attempts("X") == 0
    s.record(WatcherRecord(task_id="X", attempts=1))
    assert json.loads(p.read_text())["X"]["attempts"] == 1
