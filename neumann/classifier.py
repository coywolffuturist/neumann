"""TokenClassifier — classifies raw text chunks into TokenTypes.

Rules are loaded from rules/token_rules.json (data, not code).
Priority-ordered: first matching rule wins.
Pure function: no side effects, no hidden state.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .types import Token, TokenType

DEFAULT_RULES_PATH = Path(__file__).parent.parent / "rules" / "token_rules.json"


class TokenClassifier:
    def __init__(self, rules_path: Path | str | None = None) -> None:
        path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
        self._rules = self._load_rules(path)

    # ── public ────────────────────────────────────────────────

    def classify(self, raw: str) -> Token:
        """Classify a raw text chunk. Returns a Token with type and metadata."""
        for rule in self._rules:
            match = rule["compiled"].match(raw)
            if match:
                metadata = self._extract(match, rule.get("extract", {}))
                return Token(type=rule["type"], raw=raw, metadata=metadata)
        # Should never reach here — rules must include a catch-all
        return Token(type=TokenType.UNKNOWN, raw=raw)

    # ── private ───────────────────────────────────────────────

    @staticmethod
    def _load_rules(path: Path) -> list[dict[str, Any]]:
        with open(path) as f:
            raw = json.load(f)
        rules = sorted(raw, key=lambda r: r["priority"])
        for rule in rules:
            rule["compiled"] = re.compile(rule["pattern"], re.MULTILINE)
            rule["type"] = TokenType(rule["type"])
        return rules

    @staticmethod
    def _extract(match: re.Match, extract_spec: dict[str, int]) -> dict[str, Any]:
        """Extract named metadata from regex capture groups."""
        metadata: dict[str, Any] = {}
        for key, group_index in extract_spec.items():
            try:
                metadata[key] = match.group(group_index)
            except IndexError:
                metadata[key] = None
        return metadata
