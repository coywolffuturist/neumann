"""FormatSelector — dispatches (TokenType, RenderContext) → formatter name.

Dispatch table loaded from rules/dispatch.json.
Pure function: no side effects.
Wildcard "*" matches any type or context.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import Token, RenderContext, RoutingDecision

DEFAULT_DISPATCH_PATH = Path(__file__).parent.parent / "rules" / "dispatch.json"


class FormatSelector:
    def __init__(self, dispatch_path: Path | str | None = None) -> None:
        path = Path(dispatch_path) if dispatch_path else DEFAULT_DISPATCH_PATH
        self._table = self._load(path)

    # ── public ────────────────────────────────────────────────

    def select(self, token: Token, context: RenderContext) -> RoutingDecision:
        """Return the best matching RoutingDecision for a token+context pair."""
        candidates = [
            row for row in self._table
            if self._matches(row, token.type.value, context.value)
        ]
        if not candidates:
            return RoutingDecision(
                formatter="FallbackHandler",
                context=context,
                priority=99,
                trace=[f"no match for ({token.type}, {context}) — fallback"],
            )
        best = min(candidates, key=lambda r: r["priority"])
        return RoutingDecision(
            formatter=best["formatter"],
            context=context,
            priority=best["priority"],
            trace=[f"matched ({token.type.value}, {context.value}) → {best['formatter']}"],
        )

    # ── private ───────────────────────────────────────────────

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _matches(row: dict[str, Any], token_type: str, context: str) -> bool:
        t_match = row["type"] in (token_type, "*")
        c_match = row["context"] in (context, "*")
        return t_match and c_match
