"""ShapeClassifier — does this prompt describe a single task or a multi-task mission?

Pure function. Rules loaded from ``rules/shape_rules.json``. Pattern matching is
case-insensitive and multi-line. Sentence-count rule is a special case: the
``"sentence_count_threshold"`` match_via fires when the regex hits ``>= 2``
times (i.e. ``>= 3`` substantive sentences).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .types import Shape, ShapeDecision

DEFAULT_RULES_PATH = Path(__file__).parent / "rules" / "shape_rules.json"

# Sentence-count rule needs >= this many regex hits to escalate to mission shape.
# Two hits = three sentences (between-sentence boundaries), which is our threshold.
SENTENCE_COUNT_THRESHOLD = 2


class ShapeClassifier:
    def __init__(self, rules_path: Path | str | None = None) -> None:
        path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
        self._rules = self._load(path)

    # ── public ────────────────────────────────────────────────

    def classify(self, prompt: str) -> ShapeDecision:
        """Classify a raw user prompt as ``single-task`` or ``mission``.

        Rules are evaluated in priority order. First match wins. The very last
        rule must be a ``.*`` catch-all that resolves to ``single-task``.
        """
        sentence_count = 0
        for rule in self._rules:
            match_via = rule.get("match_via")
            if match_via == "sentence_count_threshold":
                hits = len(rule["compiled"].findall(prompt))
                if hits >= SENTENCE_COUNT_THRESHOLD:
                    return ShapeDecision(
                        shape=Shape(rule["shape"]),
                        matched_rule_priority=rule["priority"],
                        matched_rule_note=rule.get("note", ""),
                        sentence_count=hits + 1,  # N boundaries = N+1 sentences
                    )
                # Not enough hits — keep going.
                sentence_count = hits + 1
                continue

            if rule["compiled"].search(prompt):
                return ShapeDecision(
                    shape=Shape(rule["shape"]),
                    matched_rule_priority=rule["priority"],
                    matched_rule_note=rule.get("note", ""),
                    sentence_count=sentence_count,
                )

        # Should never reach here — rules must include a catch-all.
        return ShapeDecision(
            shape=Shape.SINGLE_TASK,
            matched_rule_priority=999,
            matched_rule_note="no rule matched (catch-all missing!)",
            sentence_count=sentence_count,
        )

    # ── private ───────────────────────────────────────────────

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        with open(path) as f:
            raw = json.load(f)
        rules = sorted(raw, key=lambda r: r["priority"])
        for rule in rules:
            rule["compiled"] = re.compile(rule["pattern"], re.IGNORECASE | re.MULTILINE)
        return rules
