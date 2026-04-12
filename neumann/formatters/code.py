"""CodeBlockRenderer — renders code blocks and inline code.

Terminal: ANSI color syntax highlighting (basic keyword coloring)
API/Agent: strip fences, return raw code
Web: wrap in <pre><code class="language-X">
IDE: return raw with language metadata preserved
"""
from __future__ import annotations
import re
from ..types import Token, RenderContext
from . import Formatter

# Basic ANSI colors for terminal syntax highlighting
_RESET  = "\033[0m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_MAGENTA= "\033[35m"
_RED    = "\033[31m"
_DIM    = "\033[2m"

# Simple keyword sets per language
_KEYWORDS = {
    "python": {"def","class","import","from","return","if","else","elif","for","while",
               "with","as","try","except","finally","raise","pass","yield","lambda","True","False","None"},
    "javascript": {"function","const","let","var","return","if","else","for","while",
                   "class","import","export","from","async","await","true","false","null","undefined"},
    "typescript": {"function","const","let","var","return","if","else","for","while",
                   "class","import","export","from","async","await","interface","type",
                   "true","false","null","undefined","extends","implements"},
    "bash": {"if","then","else","fi","for","do","done","while","case","esac","function",
             "return","export","echo","local"},
}

_STRING_RE = re.compile(r'(\'[^\'\\]*(?:\\.[^\'\\]*)*\'|"[^"\\]*(?:\\.[^"\\]*)*")')
_COMMENT_RE = {
    "python":     re.compile(r'(#.*)$', re.MULTILINE),
    "javascript": re.compile(r'(//.*|/\*[\s\S]*?\*/)'),
    "typescript": re.compile(r'(//.*|/\*[\s\S]*?\*/)'),
    "bash":       re.compile(r'(#.*)$', re.MULTILINE),
}


def _highlight_terminal(code: str, language: str) -> str:
    lang = (language or "").lower()
    keywords = _KEYWORDS.get(lang, set())
    lines = []
    for line in code.splitlines():
        # Comments
        comment_re = _COMMENT_RE.get(lang)
        if comment_re:
            line = comment_re.sub(lambda m: _DIM + m.group(0) + _RESET, line)
        # Strings
        line = _STRING_RE.sub(lambda m: _GREEN + m.group(0) + _RESET, line)
        # Keywords (word boundary match)
        for kw in keywords:
            line = re.sub(rf'\b({re.escape(kw)})\b', _CYAN + r'\1' + _RESET, line)
        lines.append(line)
    return "\n".join(lines)


class CodeBlockRenderer(Formatter):
    def render(self, token: Token, context: RenderContext) -> str:
        raw = token.raw
        language = token.metadata.get("language") or ""

        # Strip fences to get just the code
        code = re.sub(r'^```\w*\n?', '', raw)
        code = re.sub(r'\n?```$', '', code).strip()

        if context == RenderContext.TERMINAL:
            lang_label = f" {_DIM}[{language}]{_RESET}" if language else ""
            highlighted = _highlight_terminal(code, language)
            border = _CYAN + "─" * 60 + _RESET
            return f"{border}{lang_label}\n{highlighted}\n{border}"

        elif context == RenderContext.WEB_HTML:
            lang_class = f' class="language-{language}"' if language else ""
            escaped = code.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            return f'<pre><code{lang_class}>{escaped}</code></pre>'

        elif context == RenderContext.IDE:
            return f"```{language}\n{code}\n```"

        else:  # API_JSON, AGENT — return raw code, no decoration
            return code
