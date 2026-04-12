"""MarkdownRenderer — context-aware markdown processing.

Terminal: strip markdown syntax, preserve structure as plain text with ANSI headers
Web:      convert to HTML
API/JSON: strip all formatting, return plain text
Agent:    pass through as-is
"""
from __future__ import annotations
import re
from ..types import Token, RenderContext
from . import Formatter

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"
_YELLOW = "\033[33m"


class MarkdownRenderer(Formatter):
    def render(self, token: Token, context: RenderContext) -> str:
        raw = token.raw

        if context == RenderContext.TERMINAL:
            return self._terminal(raw)
        elif context == RenderContext.WEB_HTML:
            return self._html(raw)
        elif context in (RenderContext.API_JSON, RenderContext.AGENT):
            return self._plain(raw)
        else:
            return raw

    def _terminal(self, text: str) -> str:
        lines = []
        for line in text.splitlines():
            # ATX headings
            m = re.match(r'^(#{1,6})\s+(.*)', line)
            if m:
                level = len(m.group(1))
                content = m.group(2)
                prefix = _CYAN + _BOLD + "#" * level + " " + _RESET + _BOLD
                lines.append(f"{prefix}{content}{_RESET}")
                continue
            # Bold **text**
            line = re.sub(r'\*\*(.+?)\*\*', _BOLD + r'\1' + _RESET, line)
            # Italic *text*
            line = re.sub(r'\*(.+?)\*', _DIM + r'\1' + _RESET, line)
            # Inline code `text`
            line = re.sub(r'`([^`]+)`', _YELLOW + r'\1' + _RESET, line)
            # Unordered list
            line = re.sub(r'^[-*+]\s+', "  • ", line)
            # Horizontal rule
            if re.match(r'^-{3,}$', line.strip()):
                line = _DIM + "─" * 60 + _RESET
            lines.append(line)
        return "\n".join(lines)

    def _html(self, text: str) -> str:
        # Minimal markdown → HTML (headings, bold, italic, code, lists, hr)
        lines = []
        in_list = False
        for line in text.splitlines():
            # Headings
            m = re.match(r'^(#{1,6})\s+(.*)', line)
            if m:
                if in_list:
                    lines.append("</ul>"); in_list = False
                level = len(m.group(1))
                lines.append(f"<h{level}>{m.group(2)}</h{level}>")
                continue
            # HR
            if re.match(r'^-{3,}$', line.strip()):
                lines.append("<hr>"); continue
            # List items
            m = re.match(r'^[-*+]\s+(.*)', line)
            if m:
                if not in_list:
                    lines.append("<ul>"); in_list = True
                content = self._inline_html(m.group(1))
                lines.append(f"<li>{content}</li>")
                continue
            if in_list:
                lines.append("</ul>"); in_list = False
            if line.strip():
                lines.append(f"<p>{self._inline_html(line)}</p>")
        if in_list:
            lines.append("</ul>")
        return "\n".join(lines)

    def _plain(self, text: str) -> str:
        # Strip all markdown syntax
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^-{3,}$', '', text, flags=re.MULTILINE)
        return text.strip()

    @staticmethod
    def _inline_html(text: str) -> str:
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        return text
