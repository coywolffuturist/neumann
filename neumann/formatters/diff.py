"""DiffRenderer — renders unified diffs.

Terminal: ANSI colored (green additions, red deletions, cyan headers)
Web:      HTML with colored spans
API/Agent: passthrough, clean unified diff text
"""
from __future__ import annotations
import re
from ..types import Token, RenderContext
from . import Formatter

_RESET  = "\033[0m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"


class DiffRenderer(Formatter):
    def render(self, token: Token, context: RenderContext) -> str:
        lines = token.raw.splitlines()

        if context == RenderContext.TERMINAL:
            return self._terminal(lines)
        elif context == RenderContext.WEB_HTML:
            return self._html(lines)
        else:
            return token.raw  # passthrough for API / agent

    def _terminal(self, lines: list[str]) -> str:
        out = []
        for line in lines:
            if line.startswith("+++") or line.startswith("---"):
                out.append(_DIM + line + _RESET)
            elif line.startswith("@@"):
                out.append(_CYAN + line + _RESET)
            elif line.startswith("+"):
                out.append(_GREEN + line + _RESET)
            elif line.startswith("-"):
                out.append(_RED + line + _RESET)
            else:
                out.append(line)
        return "\n".join(out)

    def _html(self, lines: list[str]) -> str:
        out = ['<div class="diff">']
        for line in lines:
            escaped = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            if line.startswith("+++") or line.startswith("---"):
                out.append(f'<div class="diff-file">{escaped}</div>')
            elif line.startswith("@@"):
                out.append(f'<div class="diff-hunk">{escaped}</div>')
            elif line.startswith("+"):
                out.append(f'<div class="diff-add">{escaped}</div>')
            elif line.startswith("-"):
                out.append(f'<div class="diff-del">{escaped}</div>')
            else:
                out.append(f'<div class="diff-ctx">{escaped}</div>')
        out.append("</div>")
        return "\n".join(out)
