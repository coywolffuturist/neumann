"""SchemaValidator — deterministic output gate before emission.

Validates that output satisfies a declared contract.
No LLM involvement. Pure symbolic check.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .types import ValidationResult


class SchemaValidator:
    def validate(self, output: str, schema: dict[str, Any]) -> ValidationResult:
        """Validate output against a schema dict.

        Supported schema keys:
        - type: "json" | "text" | "code"
        - max_length: int
        - min_length: int
        - pattern: regex string that output must match
        - forbidden: list of regex strings that must NOT appear
        - required_keys: list of JSON keys that must be present (for type=json)
        """
        schema_type = schema.get("type", "text")

        if schema_type == "json":
            result = self._validate_json(output, schema)
            if not result.valid:
                return result

        max_len = schema.get("max_length")
        if max_len and len(output) > max_len:
            return ValidationResult(
                valid=False,
                reason=f"Output length {len(output)} exceeds max {max_len}",
                severity="error",
            )

        min_len = schema.get("min_length")
        if min_len and len(output) < min_len:
            return ValidationResult(
                valid=False,
                reason=f"Output length {len(output)} below min {min_len}",
                severity="warn",
            )

        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, output):
            return ValidationResult(
                valid=False,
                reason=f"Output does not match required pattern: {pattern}",
                severity="error",
            )

        for forbidden in schema.get("forbidden", []):
            if re.search(forbidden, output):
                return ValidationResult(
                    valid=False,
                    reason=f"Output contains forbidden pattern: {forbidden}",
                    severity="fatal",
                )

        return ValidationResult(valid=True)

    # ── private ───────────────────────────────────────────────

    @staticmethod
    def _validate_json(output: str, schema: dict[str, Any]) -> ValidationResult:
        try:
            data = json.loads(output)
        except json.JSONDecodeError as e:
            return ValidationResult(valid=False, reason=f"Invalid JSON: {e}", severity="error")

        for key in schema.get("required_keys", []):
            if key not in data:
                return ValidationResult(
                    valid=False,
                    reason=f"Missing required JSON key: {key}",
                    severity="error",
                )
        return ValidationResult(valid=True)
