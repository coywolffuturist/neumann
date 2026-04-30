"""Decomposer — split oversized PlannedTasks into micro-tasks before routing.

Sits between the Planner and the per-task routing loop. When a planned task
exceeds the complexity thresholds in ``rules/decomposition_rules.json``, the
Decomposer fans it out into N child tasks (one per target file by default)
plus one integration task that ``depends_on`` all of the children.

The principle: a task that fits in a single executor session has high
probability of success; a task that doesn't has thrash-prone failure modes
(see Brendan's auto-memory ``feedback_micro_tasks_high_probability.md``).
The Decomposer enforces this principle as a deterministic pipeline stage,
so every surface (Slack, Lucid, Fusion intake) inherits the behavior for
free.

Pure function over the Plan. No I/O beyond the rules JSON read at init.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from .types import Plan, PlannedTask

DEFAULT_RULES_PATH = Path(__file__).parent / "rules" / "decomposition_rules.json"

# Default thresholds applied when the rules file is missing or empty.
# Rules keys mirror these names exactly.
_DEFAULT_THRESHOLDS = {
    "max_lines_estimate": 500,
    "max_files_changed": 3,
    "max_distinct_outputs": 2,
}

# Verbs that count as "distinct outputs" when found in the task description.
# Rough heuristic — refined as we see real planner output.
_OUTPUT_VERB_RE = re.compile(
    r"\b(create|add|implement|build|generate|write|extract|introduce)\b",
    re.IGNORECASE,
)

# Rough conversion: 50 description characters ≈ 1 LOC of resulting code.
# Pessimistic — better to over-flag than under-flag (false positives just
# create more micro-tasks; false negatives recreate the FN-009 thrash).
_CHARS_PER_LINE_ESTIMATE = 50


class Decomposer:
    """Splits oversized PlannedTasks into micro-tasks + integration task.

    Construct with no args to use the bundled rules JSON, or pass a
    custom rules_path / inline thresholds dict for tests.
    """

    def __init__(
        self,
        rules_path: Path | str | None = None,
        thresholds: dict[str, int] | None = None,
    ) -> None:
        if thresholds is not None:
            self._thresholds = {**_DEFAULT_THRESHOLDS, **thresholds}
        else:
            self._thresholds = self._load_rules(
                Path(rules_path) if rules_path else DEFAULT_RULES_PATH
            )

    # ── public surface ────────────────────────────────────────

    def decompose(self, plan: Plan) -> Plan:
        """Return a Plan with oversized tasks split into children + integration.

        Plans whose tasks all fit under threshold are returned unchanged
        (object identity is not preserved — a fresh Plan is built — but
        the task list content is identical).
        """
        new_tasks: list[PlannedTask] = []
        for task in plan.tasks:
            if not self._exceeds_threshold(task):
                new_tasks.append(task)
                continue
            children, integration = self._split(task)
            new_tasks.extend(children)
            new_tasks.append(integration)

        return replace(plan, tasks=tuple(new_tasks))

    # ── internals ─────────────────────────────────────────────

    def _exceeds_threshold(self, task: PlannedTask) -> bool:
        if len(task.target_files) > self._thresholds["max_files_changed"]:
            return True
        if self._count_distinct_outputs(task) > self._thresholds["max_distinct_outputs"]:
            return True
        if self._estimate_lines(task) > self._thresholds["max_lines_estimate"]:
            return True
        return False

    def _split(
        self, task: PlannedTask
    ) -> tuple[list[PlannedTask], PlannedTask]:
        """Fan out one oversized task into N children + 1 integration task."""
        parent_id = self._task_id(task.title, "")
        files = task.target_files or ("(unspecified file)",)

        children: list[PlannedTask] = []
        for idx, target_file in enumerate(files):
            child_id = f"{parent_id}-c{idx}"
            child_title = f"{task.title} — {self._short_file_label(target_file)}"
            child_description = (
                f"Subset of parent task '{task.title}'.\n"
                f"This child handles a single file: {target_file}.\n\n"
                f"Parent description (for context):\n{task.description}"
            )
            children.append(
                PlannedTask(
                    title=child_title,
                    description=child_description,
                    type_hints=task.type_hints,
                    target_files=(target_file,) if target_file != "(unspecified file)" else (),
                    acceptance_criteria=task.acceptance_criteria,
                    depends_on=task.depends_on,
                    extra={
                        **task.extra,
                        "parent_task_id": parent_id,
                        "task_id": child_id,
                        "decomposed": True,
                    },
                )
            )

        integration_id = f"{parent_id}-int"
        integration_title = f"{task.title} — integration"
        integration_description = (
            f"Integration task for the decomposed parent '{task.title}'.\n"
            f"Wire up the {len(children)} child task(s) into a coherent whole.\n"
            "Run after every child has produced its output. Do not begin until "
            "all children are merged."
        )
        integration = PlannedTask(
            title=integration_title,
            description=integration_description,
            type_hints=task.type_hints,
            target_files=(),
            acceptance_criteria=task.acceptance_criteria,
            depends_on=tuple(c.extra["task_id"] for c in children),
            extra={
                **task.extra,
                "parent_task_id": parent_id,
                "task_id": integration_id,
                "decomposed": True,
                "is_integration": True,
            },
        )
        return children, integration

    @staticmethod
    def _count_distinct_outputs(task: PlannedTask) -> int:
        text = " ".join(
            (
                task.title,
                task.description,
                task.acceptance_criteria,
            )
        )
        return len(_OUTPUT_VERB_RE.findall(text))

    @staticmethod
    def _estimate_lines(task: PlannedTask) -> int:
        # Use both description and acceptance_criteria — the planner often
        # puts the meaty bits in one or the other.
        char_count = len(task.description) + len(task.acceptance_criteria)
        return char_count // _CHARS_PER_LINE_ESTIMATE

    @staticmethod
    def _task_id(title: str, salt: str) -> str:
        h = hashlib.sha256((title + "|" + salt).encode("utf-8")).hexdigest()
        return f"dt-{h[:10]}"

    @staticmethod
    def _short_file_label(path: str) -> str:
        # Keep enough of the path to distinguish files with same basename.
        # "app/routes/intel.js" → "routes/intel.js"; bare names stay bare.
        parts = path.split("/")
        return "/".join(parts[-2:]) if len(parts) >= 2 else path

    @staticmethod
    def _load_rules(path: Path) -> dict[str, int]:
        """Read thresholds from JSON. Missing/malformed file → defaults."""
        if not path.exists():
            return dict(_DEFAULT_THRESHOLDS)
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return dict(_DEFAULT_THRESHOLDS)
        if not isinstance(raw, dict):
            return dict(_DEFAULT_THRESHOLDS)
        # Coerce only the recognized keys; ignore unknown keys silently
        # (forward-compat with rule additions in newer files).
        merged = dict(_DEFAULT_THRESHOLDS)
        for key in _DEFAULT_THRESHOLDS:
            value = raw.get(key)
            if isinstance(value, int) and value > 0:
                merged[key] = value
        return merged
