"""ContextResolver — derives a RoutingContext from environment + task signals.

Pure function. The default resolver inspects ``target_files`` heuristics on
the PlannedTask itself. Callers that have richer context (Fusion's project
config, available agents) can subclass and override ``resolve``.
"""
from __future__ import annotations

from typing import Any

from .types import PlannedTask, RoutingContext


# Heuristic: file paths that strongly imply a project type.
_FRONTEND_PATH_HINTS = (".tsx", ".jsx", ".vue", ".svelte", "app/public/", "/components/", "/styles/")
_BACKEND_PATH_HINTS = ("server.js", "server.ts", "/api/", "/routes/", "/migrations/", ".sql", "/services/", "/controllers/", "/models/", "/middleware/")
_TEST_PATH_HINTS = ("/tests/", "/__tests__/", ".spec.", ".test.")


class ContextResolver:
    def __init__(self, available_personas: tuple[str, ...] = (), persona_load: dict[str, int] | None = None) -> None:
        self._available = available_personas
        self._load = dict(persona_load or {})

    # ── public ────────────────────────────────────────────────

    def resolve(self, task: PlannedTask, env: dict[str, Any] | None = None) -> RoutingContext:
        env = env or {}
        project_type = env.get("project_type") or self._infer_project_type(task) or "*"
        return RoutingContext(
            project_type=project_type,
            available_personas=tuple(env.get("available_personas") or self._available),
            persona_load=dict(env.get("persona_load") or self._load),
            extra={k: v for k, v in env.items() if k not in {"project_type", "available_personas", "persona_load"}},
        )

    # ── private ───────────────────────────────────────────────

    @staticmethod
    def _infer_project_type(task: PlannedTask) -> str | None:
        files = "\n".join(task.target_files).lower()
        if not files:
            return None
        is_test = any(h in files for h in _TEST_PATH_HINTS)
        is_frontend = any(h in files for h in _FRONTEND_PATH_HINTS)
        is_backend = any(h in files for h in _BACKEND_PATH_HINTS)
        if is_test and not is_frontend and not is_backend:
            return "test-project"
        if is_frontend and not is_backend:
            return "frontend-project"
        if is_backend and not is_frontend:
            return "backend-project"
        # Mixed or none — let dispatch fall through to wildcard rows.
        return None
