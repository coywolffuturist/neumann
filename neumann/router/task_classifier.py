"""TaskTypeClassifier — classifies a structured PlannedTask into a TaskType.

Rules loaded from ``rules/task_type_rules.json``. Each rule declares which
``match_fields`` of the PlannedTask it scans against; e.g. ``target_files``
is a much stronger signal than prose, so file-extension rules sit at high
priority.

Pure function. Same input → same output.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .types import PlannedTask, TaskType

DEFAULT_RULES_PATH = Path(__file__).parent / "rules" / "task_type_rules.json"

DEFAULT_FIELDS: tuple[str, ...] = ("title", "description", "type_hints")


class TaskTypeClassifier:
    def __init__(self, rules_path: Path | str | None = None) -> None:
        path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
        self._rules = self._load(path)

    # ── public ────────────────────────────────────────────────

    def classify(self, task: PlannedTask) -> tuple[TaskType, dict[str, Any]]:
        """Return ``(task_type, matched_rule_metadata)``.

        The metadata dict carries the priority and note of the matched rule
        for downstream tracing.
        """
        for rule in self._rules:
            fields = tuple(rule.get("match_fields") or DEFAULT_FIELDS)
            text = task.match_text(fields)
            if not text:
                continue
            if rule["compiled"].search(text):
                return TaskType(rule["type"]), {
                    "priority": rule["priority"],
                    "fields": fields,
                    "note": rule.get("note", ""),
                }
        # Catch-all should always match — but defensive default.
        return TaskType.UNKNOWN, {"priority": 999, "fields": (), "note": "no rule matched"}

    # ── private ───────────────────────────────────────────────

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        with open(path) as f:
            raw = json.load(f)
        rules = sorted(raw, key=lambda r: r["priority"])
        for rule in rules:
            rule["compiled"] = re.compile(rule["pattern"], re.IGNORECASE | re.MULTILINE)
        return rules
