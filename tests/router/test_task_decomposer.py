"""Tests for ``neumann.router.task_decomposer.TaskDecomposer``.

The TaskDecomposer's contract:
- Read thresholds from the rules JSON (or use defaults on missing/bad file).
- Tasks within all thresholds pass through unchanged (Plan identity preserved
  when no splits happen — single replace() avoids per-task allocation churn).
- Tasks exceeding ANY threshold are replaced with N children + 1 integration
  task. Children inherit deps + carry parent_task_id in extra. Integration
  depends on every child in insertion order.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neumann.router.task_decomposer import (
    DEFAULT_RULES,
    DEFAULT_RULES_PATH,
    TaskDecomposer,
)
from neumann.router.types import Plan, PlannedTask


# ── helpers ────────────────────────────────────────────────────────────────


def _make_task(
    title: str = "T",
    *,
    target_files: tuple[str, ...] = (),
    depends_on: tuple[str, ...] = (),
    extra: dict | None = None,
    description: str = "do the thing",
    type_hints: tuple[str, ...] = (),
    acceptance_criteria: str = "tests pass",
) -> PlannedTask:
    return PlannedTask(
        title=title,
        description=description,
        type_hints=type_hints,
        target_files=target_files,
        acceptance_criteria=acceptance_criteria,
        depends_on=depends_on,
        extra=dict(extra or {}),
    )


def _make_plan(*tasks: PlannedTask) -> Plan:
    return Plan(mission_title="test", summary="", assumptions=(), tasks=tasks)


# ── threshold detection ───────────────────────────────────────────────────


def test_small_task_passes_through(tmp_path: Path) -> None:
    """A task within all thresholds is returned in the Plan unchanged
    (same instance — Plan.tasks is a frozen tuple)."""
    task = _make_task(
        target_files=("a.py", "b.py"),
        extra={"lines_estimate": 100, "distinct_outputs": 1},
    )
    plan = _make_plan(task)
    out = TaskDecomposer().decompose(plan)
    assert out.tasks == (task,)
    assert out is plan  # no work done → original plan returned


def test_oversized_by_file_count_splits(tmp_path: Path) -> None:
    """4 target_files with default max_files_changed=3 → split."""
    task = _make_task(target_files=("a.py", "b.py", "c.py", "d.py"))
    out = TaskDecomposer().decompose(_make_plan(task))
    # 4 children + 1 integration = 5 tasks
    assert len(out.tasks) == 5
    children, integration = out.tasks[:-1], out.tasks[-1]
    assert all(c.title.startswith("T – part ") for c in children)
    assert all(len(c.target_files) == 1 for c in children)
    assert integration.title == "T – integration"
    assert integration.depends_on == tuple(c.title for c in children)


def test_oversized_by_lines_estimate_splits(monkeypatch, tmp_path: Path) -> None:
    """600 lines_estimate exceeds default 500 → split even with 2 files."""
    task = _make_task(
        target_files=("a.py", "b.py"),
        extra={"lines_estimate": 600},
    )
    out = TaskDecomposer().decompose(_make_plan(task))
    assert len(out.tasks) == 3  # 2 children + 1 integration
    children, integration = out.tasks[:-1], out.tasks[-1]
    for c in children:
        assert c.extra["parent_task_id"] == "T"
    assert integration.depends_on == tuple(c.title for c in children)


def test_oversized_by_distinct_outputs_splits() -> None:
    """3 distinct_outputs exceeds default 2 → split."""
    task = _make_task(
        target_files=("a.py", "b.py"),
        extra={"distinct_outputs": 3},
    )
    out = TaskDecomposer().decompose(_make_plan(task))
    assert len(out.tasks) == 3


def test_integration_task_depends_on_all_children() -> None:
    """Integration task's depends_on equals tuple of all child titles in
    insertion order (matching task.target_files)."""
    files = ("x.py", "y.py", "z.py", "w.py")
    task = _make_task(target_files=files)
    out = TaskDecomposer().decompose(_make_plan(task))
    children, integration = out.tasks[:-1], out.tasks[-1]
    assert integration.title.endswith(" – integration")
    assert integration.depends_on == tuple(c.title for c in children)
    # Each child has its own single file, in original order.
    assert tuple(c.target_files for c in children) == tuple((f,) for f in files)


def test_integration_inherits_full_target_files() -> None:
    files = ("a.py", "b.py", "c.py", "d.py")
    task = _make_task(target_files=files, acceptance_criteria="all 4 modules ship")
    out = TaskDecomposer().decompose(_make_plan(task))
    integration = out.tasks[-1]
    assert integration.target_files == files
    assert integration.acceptance_criteria == "all 4 modules ship"
    assert integration.type_hints == ("integration",)


def test_children_inherit_parent_depends_on() -> None:
    parent_deps = ("FN-PRE-1", "FN-PRE-2")
    task = _make_task(
        target_files=("a.py", "b.py", "c.py", "d.py"),
        depends_on=parent_deps,
    )
    out = TaskDecomposer().decompose(_make_plan(task))
    children = out.tasks[:-1]
    for c in children:
        assert c.depends_on == parent_deps


def test_empty_target_files_produces_two_placeholder_children() -> None:
    """If target_files is empty but the task is still oversized via another
    threshold, produce 2 placeholder children + integration."""
    task = _make_task(target_files=(), extra={"distinct_outputs": 5})
    out = TaskDecomposer().decompose(_make_plan(task))
    assert len(out.tasks) == 3
    children, integration = out.tasks[:-1], out.tasks[-1]
    assert all(c.target_files == () for c in children)
    assert integration.depends_on == tuple(c.title for c in children)


# ── rules loading ────────────────────────────────────────────────────────


def test_missing_rules_file_uses_defaults(tmp_path: Path) -> None:
    """Pointing at a non-existent rules file falls back to DEFAULT_RULES."""
    decomp = TaskDecomposer(rules_path=tmp_path / "nope.json")
    rules = decomp._load_rules()
    assert rules == DEFAULT_RULES


def test_malformed_rules_file_uses_defaults(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not valid json {")
    decomp = TaskDecomposer(rules_path=bad)
    rules = decomp._load_rules()
    assert rules == DEFAULT_RULES


def test_partial_rules_file_merges_with_defaults(tmp_path: Path) -> None:
    """Override only one field; others fill in from DEFAULT_RULES."""
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"max_files_changed": 1}))
    decomp = TaskDecomposer(rules_path=partial)
    rules = decomp._load_rules()
    assert rules["max_files_changed"] == 1
    assert rules["max_lines_estimate"] == DEFAULT_RULES["max_lines_estimate"]
    assert rules["max_distinct_outputs"] == DEFAULT_RULES["max_distinct_outputs"]


def test_repo_default_rules_file_exists_and_parses() -> None:
    """The shipped rules file matches the documented defaults — guards
    against drift between code and config."""
    assert DEFAULT_RULES_PATH.exists(), f"missing {DEFAULT_RULES_PATH}"
    data = json.loads(DEFAULT_RULES_PATH.read_text())
    assert set(data.keys()) == set(DEFAULT_RULES.keys())
    assert data == DEFAULT_RULES


# ── pass-through / mixed plans ────────────────────────────────────────────


def test_mixed_plan_only_oversized_split() -> None:
    """A Plan with one big task + one small task: only the big one gets
    expanded; the small one passes through in its original position-ish."""
    big = _make_task(title="BIG", target_files=("a.py", "b.py", "c.py", "d.py"))
    small = _make_task(title="SMALL", target_files=("z.py",))
    out = TaskDecomposer().decompose(_make_plan(big, small))
    titles = [t.title for t in out.tasks]
    # 4 children, 1 integration, then small
    assert titles == [
        "BIG – part 1", "BIG – part 2", "BIG – part 3", "BIG – part 4",
        "BIG – integration",
        "SMALL",
    ]


def test_extra_field_with_string_lines_estimate_is_treated_as_zero() -> None:
    """Defensive int() coercion — a stringified number from JSON gets parsed,
    a non-numeric string falls through as 0 (no split)."""
    task = _make_task(target_files=("a.py",), extra={"lines_estimate": "garbage"})
    out = TaskDecomposer().decompose(_make_plan(task))
    assert out.tasks == (task,)
