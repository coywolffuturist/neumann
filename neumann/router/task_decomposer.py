"""TaskDecomposer — split oversized ``PlannedTask``s into children + integration.

This is the post-Planner companion to the intent-level ``Decomposer``
in ``decomposer.py``. The intent-level Decomposer keeps Planner inputs
small. This task-level TaskDecomposer keeps executor inputs small —
when a single PlannedTask covers too many files, lines, or distinct
outputs, the executor stalls (FN-009 trap: sub-agent delegation,
infinite worktree thrash). Splitting at the task boundary forces each
executor invocation to see one focused chunk.

Stage placement:
    Prompt → Shape → Interview → Decomposer (intent) → Planner →
    >>> TaskDecomposer (this) <<< → Per-task routing.

Rules live in ``neumann/router/rules/task_decomposition_rules.json``.
Defaults: max_lines_estimate=500, max_files_changed=3,
max_distinct_outputs=2.

A task is "oversized" when ANY of:
  - ``task.extra.get("lines_estimate", 0) > max_lines_estimate``
  - ``len(task.target_files) > max_files_changed``
  - ``task.extra.get("distinct_outputs", 0) > max_distinct_outputs``

Oversized tasks are replaced with:
  - N **child tasks** (one per ``target_files`` entry, or 2 placeholders
    if ``target_files`` is empty), each carrying ``parent_task_id``
    in ``extra`` and inheriting the parent's ``depends_on``.
  - One **integration task** that depends on all N children.

Non-oversized tasks pass through unchanged.

Per FN-002: this is named TaskDecomposer (not Decomposer) and lives in
its own module to avoid colliding with the existing intent-level
Decomposer class — same word, different stage.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from .types import Plan, PlannedTask

log = logging.getLogger("neumann.router.task_decomposer")


DEFAULT_RULES_PATH = Path(__file__).parent / "rules" / "task_decomposition_rules.json"


def _coerce_int(value) -> int:
    """Best-effort int coercion. The Planner's ``extra`` dict comes from
    LLM JSON and occasionally carries strings or floats where ints are
    expected. Treat anything unparseable as 0 (i.e. "no signal") so a
    weird value never crashes the dispatch loop."""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

# Defaults match neumann/router/rules/task_decomposition_rules.json. Used
# verbatim when the config file is missing or malformed — keeps the
# pipeline running on a fresh checkout before anyone seeds the rules.
DEFAULT_RULES = {
    "max_lines_estimate": 500,
    "max_files_changed": 3,
    "max_distinct_outputs": 2,
}


@dataclass(frozen=True)
class TaskDecomposer:
    """Splits oversized PlannedTasks into children + an integration task.

    ``rules_path`` is read once per ``decompose`` call (no module-level
    state) so config changes don't require a process restart. Tests
    monkey-patch ``_load_rules`` to bypass disk.
    """

    rules_path: Path | str = DEFAULT_RULES_PATH

    # ── public ────────────────────────────────────────────────────────

    def decompose(self, plan: Plan) -> Plan:
        rules = self._load_rules()
        out_tasks: list[PlannedTask] = []
        for task in plan.tasks:
            if self._is_oversized(task, rules):
                out_tasks.extend(self._split(task))
            else:
                out_tasks.append(task)
        if tuple(out_tasks) == plan.tasks:
            return plan
        return replace(plan, tasks=tuple(out_tasks))

    # ── rules ─────────────────────────────────────────────────────────

    def _load_rules(self) -> dict:
        path = Path(self.rules_path)
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            log.warning("rules unreadable at %s (%s); using defaults", path, e)
            return dict(DEFAULT_RULES)
        # Merge with defaults so partial files still produce sensible thresholds.
        merged = dict(DEFAULT_RULES)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in DEFAULT_RULES and isinstance(v, int):
                    merged[k] = v
        return merged

    # ── decision ──────────────────────────────────────────────────────

    def _is_oversized(self, task: PlannedTask, rules: dict) -> bool:
        lines = _coerce_int(task.extra.get("lines_estimate"))
        outputs = _coerce_int(task.extra.get("distinct_outputs"))
        files = len(task.target_files)
        return (
            lines > rules["max_lines_estimate"]
            or files > rules["max_files_changed"]
            or outputs > rules["max_distinct_outputs"]
        )

    # ── split ─────────────────────────────────────────────────────────

    def _split(self, task: PlannedTask) -> list[PlannedTask]:
        """Replace ``task`` with N children + 1 integration task.

        Children are 1-to-1 with ``target_files``. When ``target_files``
        is empty, produce 2 placeholder children with ``target_files=()``
        so the integration task still has something concrete to depend on.
        """
        files = task.target_files
        if not files:
            child_titles = (f"{task.title} – part 1", f"{task.title} – part 2")
            children = [
                PlannedTask(
                    title=child_title,
                    description=task.description,
                    type_hints=task.type_hints,
                    target_files=(),
                    acceptance_criteria=task.acceptance_criteria,
                    depends_on=task.depends_on,
                    extra={**task.extra, "parent_task_id": task.title},
                )
                for child_title in child_titles
            ]
        else:
            children = [
                PlannedTask(
                    title=f"{task.title} – part {i + 1}",
                    description=task.description,
                    type_hints=task.type_hints,
                    target_files=(f,),
                    acceptance_criteria=task.acceptance_criteria,
                    depends_on=task.depends_on,
                    extra={**task.extra, "parent_task_id": task.title},
                )
                for i, f in enumerate(files)
            ]

        integration = PlannedTask(
            title=f"{task.title} – integration",
            description=f"Integrate and verify all parts of: {task.title}",
            type_hints=("integration",),
            target_files=files,  # full set
            acceptance_criteria=task.acceptance_criteria,
            depends_on=tuple(c.title for c in children),
            extra={"parent_task_id": task.title},
        )

        return [*children, integration]
