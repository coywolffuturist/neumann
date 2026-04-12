"""ToolCallRenderer — renders tool invocations and results.

Parses JSON envelope: {"tool": "name", "input": {...}}
and results:          {"tool_result": "name", "output": ..., "error": ...}
"""
from __future__ import annotations
import json
from ..types import Token, RenderContext
from . import Formatter

_RESET  = "\033[0m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"


class ToolCallRenderer(Formatter):
    def render(self, token: Token, context: RenderContext) -> str:
        try:
            data = json.loads(token.raw)
        except json.JSONDecodeError:
            return token.raw

        is_result = "tool_result" in data

        if context == RenderContext.TERMINAL:
            return self._terminal(data, is_result)
        elif context == RenderContext.WEB_HTML:
            return self._html(data, is_result)
        else:
            # API / agent — pretty JSON passthrough
            return json.dumps(data, indent=2)

    def _terminal(self, data: dict, is_result: bool) -> str:
        if is_result:
            name = data.get("tool_result", "unknown")
            output = data.get("output", "")
            error = data.get("error")
            status = _RED + "✗ ERROR" + _RESET if error else _GREEN + "✓ OK" + _RESET
            content = error or output
            content_str = json.dumps(content, indent=2) if not isinstance(content, str) else content
            return (
                f"{_DIM}┌─{_RESET} {_YELLOW}tool_result{_RESET} {_BOLD}{name}{_RESET} {status}\n"
                f"{_DIM}│{_RESET}  {content_str.replace(chr(10), chr(10) + _DIM + '│' + _RESET + '  ')}\n"
                f"{_DIM}└─{_RESET}"
            )
        else:
            name = data.get("tool", "unknown")
            inputs = data.get("input", {})
            inputs_str = json.dumps(inputs, indent=2)
            return (
                f"{_DIM}┌─{_RESET} {_CYAN}tool_call{_RESET}   {_BOLD}{name}{_RESET}\n"
                f"{_DIM}│{_RESET}  {inputs_str.replace(chr(10), chr(10) + _DIM + '│' + _RESET + '  ')}\n"
                f"{_DIM}└─{_RESET}"
            )

    def _html(self, data: dict, is_result: bool) -> str:
        if is_result:
            name = data.get("tool_result", "unknown")
            output = data.get("output", "")
            error = data.get("error")
            status_class = "tool-error" if error else "tool-ok"
            content = json.dumps(error or output, indent=2)
            return (
                f'<div class="tool-result {status_class}">'
                f'<span class="tool-name">{name}</span>'
                f'<pre>{content}</pre></div>'
            )
        else:
            name = data.get("tool", "unknown")
            inputs = json.dumps(data.get("input", {}), indent=2)
            return (
                f'<div class="tool-call">'
                f'<span class="tool-name">{name}</span>'
                f'<pre>{inputs}</pre></div>'
            )
