"""FormatterRegistry — maps formatter names to instances.

Add new formatters here. One line per formatter.
"""
from __future__ import annotations
from .formatters import Formatter
from .formatters.code import CodeBlockRenderer
from .formatters.diff import DiffRenderer
from .formatters.tool_call import ToolCallRenderer
from .formatters.error import ErrorRenderer
from .formatters.markdown import MarkdownRenderer
from .formatters.agent_state import AgentStateRenderer
from .formatters.fallback import PlainTextRenderer, FallbackHandler

_REGISTRY: dict[str, Formatter] = {
    "CodeBlockRenderer":  CodeBlockRenderer(),
    "DiffRenderer":       DiffRenderer(),
    "ToolCallRenderer":   ToolCallRenderer(),
    "ErrorRenderer":      ErrorRenderer(),
    "MarkdownRenderer":   MarkdownRenderer(),
    "AgentStateRenderer": AgentStateRenderer(),
    "PlainTextRenderer":  PlainTextRenderer(),
    "FallbackHandler":    FallbackHandler(),
}


def get_formatter(name: str) -> Formatter:
    return _REGISTRY.get(name, _REGISTRY["FallbackHandler"])
