"""Tests for the Decomposer pipeline stage.

Covers:
  - below-threshold tasks pass through unchanged
  - above-threshold tasks split into children + integration
  - children carry parent_task_id and unique task_ids
  - integration task depends_on every child
  - rules JSON loader handles missing / malformed / partial files
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neumann.router import Plan, PlannedTask
from neumann.router.decomposer import Decomposer, _DEFAULT_THRESHOLDS


# ── helpers ───────────────────────────────────────────────────


def _plan(*tasks: PlannedTask) -> Plan:
    return Plan(mission_title="test mission", tasks=tuple(tasks))


def _small_task() -> PlannedTask:
    return PlannedTask(
        title="Add /api/healthz endpoint",
        description="Public health-check endpoint returning {ok:true}.",
        type_hints=("api", "endpoint"),
        target_files=("app/server.js",),
        acceptance_criteria="GET /api/healthz returns 200 with body {ok:true}",
    )


def _oversized_by_files() -> PlannedTask:
    return PlannedTask(
        title="Split server.js into per-tab routes",
        description="Refactor server into route modules.",
        type_hints=("refactor",),
        target_files=(
            "app/routes/intel.js",
            "app/routes/quests.js",
            "app/routes/pack.js",
            "app/routes/coywolf.js",
            "app/routes/wallet.js",
        ),
        acceptance_criteria="All routes mount cleanly.",
    )


# ── below-threshold passthrough ───────────────────────────────


def test_small_task_passes_through_unchanged() -> None:
    decomposer = Decomposer()
    plan = _plan(_small_task())
    out = decomposer.decompose(plan)
    assert len(out.tasks) == 1
    assert out.tasks[0] == _small_task()


def test_multiple_small_tasks_all_pass_through() -> None:
    decomposer = Decomposer()
    a = _small_task()
    b = PlannedTask(
        title="Add login button",
        description="One button.",
        target_files=("app/public/login.html",),
    )
    out = decomposer.decompose(_plan(a, b))
    assert len(out.tasks) == 2
    assert out.tasks == (a, b)


# ── above-threshold split ─────────────────────────────────────


def test_oversized_by_files_splits_into_n_plus_one() -> None:
    decomposer = Decomposer()
    parent = _oversized_by_files()
    out = decomposer.decompose(_plan(parent))
    # 5 files → 5 children + 1 integration = 6 total
    assert len(out.tasks) == 6


def test_split_children_carry_parent_id_and_unique_task_ids() -> None:
    decomposer = Decomposer()
    parent = _oversized_by_files()
    out = decomposer.decompose(_plan(parent))
    children = out.tasks[:-1]
    parent_ids = {t.extra["parent_task_id"] for t in children}
    task_ids = [t.extra["task_id"] for t in children]
    assert len(parent_ids) == 1, "all children share one parent_task_id"
    assert len(task_ids) == len(set(task_ids)), "child task_ids are unique"
    for child in children:
        assert child.extra["decomposed"] is True
        assert "is_integration" not in child.extra


def test_integration_task_depends_on_every_child() -> None:
    decomposer = Decomposer()
    parent = _oversized_by_files()
    out = decomposer.decompose(_plan(parent))
    integration = out.tasks[-1]
    children = out.tasks[:-1]
    assert integration.extra.get("is_integration") is True
    assert integration.extra["parent_task_id"] == children[0].extra["parent_task_id"]
    expected_deps = tuple(c.extra["task_id"] for c in children)
    assert integration.depends_on == expected_deps


def test_each_child_targets_exactly_one_of_the_parents_files() -> None:
    decomposer = Decomposer()
    parent = _oversized_by_files()
    out = decomposer.decompose(_plan(parent))
    children = out.tasks[:-1]
    child_files = [c.target_files for c in children]
    # Each child has exactly one target_file
    assert all(len(tf) == 1 for tf in child_files)
    # Their union == the parent's set
    assert {tf[0] for tf in child_files} == set(parent.target_files)


def test_oversized_by_distinct_outputs_triggers_split() -> None:
    # Few files but description names many distinct outputs (verb count).
    parent = PlannedTask(
        title="Build the signup pipeline",
        description=(
            "Create the signup endpoint. Add email validation. "
            "Implement the password hash flow. Build the onboarding redirect. "
            "Generate confirmation email. Write the rate-limit middleware."
        ),
        target_files=("app/server.js",),
    )
    decomposer = Decomposer()
    out = decomposer.decompose(_plan(parent))
    # Six distinct verbs > default max_distinct_outputs (2). Should split.
    assert len(out.tasks) > 1


def test_oversized_by_lines_estimate_triggers_split() -> None:
    long_description = "Implement the new module. " * 200  # ~5000 chars → ~100 lines
    parent = PlannedTask(
        title="Rewrite the engine",
        description=long_description,
        target_files=("app/engine.js",),
    )
    decomposer = Decomposer(thresholds={"max_lines_estimate": 50})
    out = decomposer.decompose(_plan(parent))
    assert len(out.tasks) > 1


# ── threshold injection ───────────────────────────────────────


def test_inline_thresholds_override_rules_file(tmp_path: Path) -> None:
    # Even if the rules file says 3, our inline override of 100 wins.
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"max_files_changed": 3}))
    decomposer = Decomposer(rules_path=rules_file, thresholds={"max_files_changed": 100})
    out = decomposer.decompose(_plan(_oversized_by_files()))
    assert len(out.tasks) == 1, "5 files < 100 threshold → no split"


# ── rules loader fault-tolerance ──────────────────────────────


def test_loader_falls_back_to_defaults_on_missing_file(tmp_path: Path) -> None:
    decomposer = Decomposer(rules_path=tmp_path / "does-not-exist.json")
    assert decomposer._thresholds == _DEFAULT_THRESHOLDS


def test_loader_falls_back_to_defaults_on_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{")
    decomposer = Decomposer(rules_path=bad)
    assert decomposer._thresholds == _DEFAULT_THRESHOLDS


def test_loader_merges_partial_rules_with_defaults(tmp_path: Path) -> None:
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"max_files_changed": 99}))
    decomposer = Decomposer(rules_path=partial)
    assert decomposer._thresholds["max_files_changed"] == 99
    # Other keys unchanged
    assert decomposer._thresholds["max_lines_estimate"] == _DEFAULT_THRESHOLDS["max_lines_estimate"]
    assert decomposer._thresholds["max_distinct_outputs"] == _DEFAULT_THRESHOLDS["max_distinct_outputs"]


def test_loader_ignores_invalid_value_types(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"max_files_changed": "lots", "max_lines_estimate": -1}))
    decomposer = Decomposer(rules_path=bogus)
    assert decomposer._thresholds == _DEFAULT_THRESHOLDS


# ── plan-level invariants ─────────────────────────────────────


def test_plan_metadata_is_preserved() -> None:
    decomposer = Decomposer()
    plan = Plan(
        mission_title="The mission",
        summary="The summary.",
        assumptions=("a1",),
        tasks=(_small_task(),),
    )
    out = decomposer.decompose(plan)
    assert out.mission_title == plan.mission_title
    assert out.summary == plan.summary
    assert out.assumptions == plan.assumptions


def test_mixed_plan_only_splits_oversized_tasks() -> None:
    decomposer = Decomposer()
    plan = _plan(_small_task(), _oversized_by_files(), _small_task())
    out = decomposer.decompose(plan)
    # 1 small + (5 children + 1 integration) + 1 small = 8
    assert len(out.tasks) == 8
    # First and last are the original small tasks (unchanged)
    assert out.tasks[0] == _small_task()
    assert out.tasks[-1] == _small_task()
