"""ContextResolver — determines the rendering/routing context from the environment.

Pure function: same env dict → same RenderContext every time.
"""
from __future__ import annotations

import os
from typing import Any

from .types import RenderContext


class ContextResolver:
    def resolve(self, env: dict[str, Any] | None = None) -> RenderContext:
        """Resolve the current RenderContext from an environment dict.

        Priority order:
        1. Explicit override in env["context"]
        2. NEUMANN_CONTEXT environment variable
        3. Detected from env flags (is_terminal, is_ide, etc.)
        4. Default: TERMINAL
        """
        e = env or {}

        # 1. Explicit override
        if "context" in e:
            return RenderContext(e["context"])

        # 2. Environment variable
        env_var = os.environ.get("NEUMANN_CONTEXT")
        if env_var:
            return RenderContext(env_var)

        # 3. Detect from flags
        if e.get("is_agent"):
            return RenderContext.AGENT
        if e.get("is_ide"):
            return RenderContext.IDE
        if e.get("is_api"):
            return RenderContext.API_JSON
        if e.get("is_web"):
            return RenderContext.WEB_HTML

        # 4. Default
        return RenderContext.TERMINAL
