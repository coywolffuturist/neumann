"""AgentStateRenderer — renders agent progress / subagent status blocks.

Parses: {"type": "agent_state", "status": "running|done|error", "label": "...", "progress": 0.0-1.0}
"""
from __future__ import annotations
import json
from ..types import Token, RenderContext
from . import Formatter

_RESET  = "\033[0m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"

_STATUS_ICONS = {
    "running": ("⟳", _YELLOW),
    "done":    ("✓", _GREEN),
    "error":   ("✗", _RED),
    "waiting": ("…", _DIM),
}

def _bar(progress: float, width: int = 20) -> str:
    filled = int(progress * width)
    return "█" * filled + "░" * (width - filled)


class AgentStateRenderer(Formatter):
    def render(self, token: Token, context: RenderContext) -> str:
        try:
            data = json.loads(token.raw)
        except json.JSONDecodeError:
            return token.raw

        status  = data.get("status", "running")
        label   = data.get("label", "Agent")
        progress = float(data.get("progress", -1))
        detail  = data.get("detail", "")

        if context == RenderContext.TERMINAL:
            return self._terminal(status, label, progress, detail)
        elif context == RenderContext.WEB_HTML:
            return self._html(status, label, progress, detail)
        else:
            return json.dumps(data, indent=2)

    def _terminal(self, status: str, label: str, progress: float, detail: str) -> str:
        icon, color = _STATUS_ICONS.get(status, ("?", _DIM))
        pct = f" {int(progress*100)}%" if progress >= 0 else ""
        bar = f" [{_bar(progress)}]" if progress >= 0 else ""
        detail_str = f"\n    {_DIM}{detail}{_RESET}" if detail else ""
        return f"{color}{icon}{_RESET} {_BOLD}{label}{_RESET}{pct}{bar}{detail_str}"

    def _html(self, status: str, label: str, progress: float, detail: str) -> str:
        pct = int(progress * 100) if progress >= 0 else 0
        bar = f'<div class="progress-bar" style="width:{pct}%"></div>' if progress >= 0 else ""
        return (
            f'<div class="agent-state agent-{status}">'
            f'<span class="agent-label">{label}</span>'
            f'<span class="agent-status">{status}</span>'
            f'<div class="progress-track">{bar}</div>'
            f'{"<p>" + detail + "</p>" if detail else ""}'
            f'</div>'
        )
