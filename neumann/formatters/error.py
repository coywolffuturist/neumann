"""ErrorRenderer — renders Python/JS errors and tracebacks.

Terminal: ANSI red header, dim traceback body, bold error message
Web:      HTML with structured error card
API/Agent: structured JSON with extracted fields
"""
from __future__ import annotations
import re
from ..types import Token, RenderContext
from . import Formatter

_RESET  = "\033[0m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"

# Patterns to extract error type and message
_ERROR_TYPE_RE = re.compile(r'^(\w+(?:Error|Exception|Warning|Fault)):\s*(.*)', re.MULTILINE)
_FILE_LINE_RE  = re.compile(r'File "(.+)", line (\d+)')


def _parse_error(raw: str) -> dict:
    type_match = _ERROR_TYPE_RE.search(raw)
    file_matches = _FILE_LINE_RE.findall(raw)
    return {
        "error_type": type_match.group(1) if type_match else "Error",
        "message":    type_match.group(2) if type_match else raw.splitlines()[0],
        "frames":     [{"file": f, "line": int(l)} for f, l in file_matches],
        "raw":        raw,
    }


class ErrorRenderer(Formatter):
    def render(self, token: Token, context: RenderContext) -> str:
        parsed = _parse_error(token.raw)

        if context == RenderContext.TERMINAL:
            return self._terminal(parsed)
        elif context == RenderContext.WEB_HTML:
            return self._html(parsed)
        else:
            # API / agent — structured JSON
            import json
            return json.dumps({
                "type": parsed["error_type"],
                "message": parsed["message"],
                "frames": parsed["frames"],
            }, indent=2)

    def _terminal(self, p: dict) -> str:
        lines = [
            f"{_RED}{_BOLD}✗ {p['error_type']}{_RESET}: {p['message']}",
        ]
        if p["frames"]:
            lines.append(f"{_DIM}  Traceback:{_RESET}")
            for frame in p["frames"]:
                lines.append(f"{_DIM}    {frame['file']}:{frame['line']}{_RESET}")
        return "\n".join(lines)

    def _html(self, p: dict) -> str:
        frames_html = "".join(
            f'<div class="error-frame">{f["file"]}:{f["line"]}</div>'
            for f in p["frames"]
        )
        return (
            f'<div class="error-block">'
            f'<div class="error-type">{p["error_type"]}</div>'
            f'<div class="error-message">{p["message"]}</div>'
            f'{frames_html}</div>'
        )
