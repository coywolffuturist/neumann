"""PersonaRegistry — persona id → metadata lookup.

Loads the 9 Fusion preset personas as defaults plus any custom personas
present under ``router/personas/*.json`` (e.g. our local Planner persona).

Mirrors ``neumann.registry`` — registry exposes the personas by id; callers
can extend by registering new entries at runtime.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PERSONAS_DIR = Path(__file__).parent / "personas"


# Fusion's 9 ship-with presets. Authoritative description matches the
# project's reference_fusion_personas memory entry.
FUSION_PRESETS: dict[str, dict[str, Any]] = {
    "ceo":                 {"name": "CEO",                 "role": "custom",   "enabled": True, "description": "Oversees project strategy, sets priorities, coordinates cross-team."},
    "cto":                 {"name": "CTO",                 "role": "custom",   "enabled": True, "description": "Defines technical architecture, evaluates technology choices, guides engineering standards."},
    "cmo":                 {"name": "CMO",                 "role": "custom",   "enabled": True, "description": "Drives product positioning, audience engagement, content strategy."},
    "cfo":                 {"name": "CFO",                 "role": "custom",   "enabled": True, "description": "Manages budget allocation, cost optimization, financial planning."},
    "engineer":            {"name": "Engineer",            "role": "engineer", "enabled": True, "description": "Implements features, fixes bugs, writes well-tested code across the stack."},
    "backend-engineer":    {"name": "Backend Engineer",    "role": "engineer", "enabled": True, "description": "Builds APIs, schemas, background processing; data integrity and reliability."},
    "frontend-engineer":   {"name": "Frontend Engineer",   "role": "engineer", "enabled": True, "description": "UI components, accessibility, responsive design."},
    "fullstack-engineer":  {"name": "Fullstack Engineer",  "role": "engineer", "enabled": True, "description": "End-to-end features, cross-layer cohesion."},
    "qa-engineer":         {"name": "QA Engineer",         "role": "engineer", "enabled": True, "description": "Designs test plans, writes automated tests, never assumes — verifies."},
}


class PersonaRegistry:
    def __init__(self, custom_dir: Path | str | None = None) -> None:
        self._table: dict[str, dict[str, Any]] = dict(FUSION_PRESETS)
        custom_path = Path(custom_dir) if custom_dir else PERSONAS_DIR
        if custom_path.exists():
            for f in sorted(custom_path.glob("*.json")):
                try:
                    with open(f) as fh:
                        record = json.load(fh)
                    pid = record.get("id") or f.stem
                    self._table[pid] = {**record, "enabled": record.get("enabled", True)}
                except (json.JSONDecodeError, OSError):
                    # Skip malformed persona files; don't poison the registry.
                    continue

    # ── public ────────────────────────────────────────────────

    def get(self, persona_id: str) -> dict[str, Any] | None:
        return self._table.get(persona_id)

    def list_ids(self) -> list[str]:
        return list(self._table.keys())

    def register(self, persona_id: str, record: dict[str, Any]) -> None:
        """Add or overwrite a persona at runtime."""
        self._table[persona_id] = {**record, "enabled": record.get("enabled", True)}


# Module-level singleton for callers that don't want to instantiate.
_DEFAULT_REGISTRY = PersonaRegistry()


def get_persona(persona_id: str) -> dict[str, Any] | None:
    return _DEFAULT_REGISTRY.get(persona_id)
