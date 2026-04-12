from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TokenType(str, Enum):
    CODE_BLOCK      = "code_block"
    INLINE_CODE     = "inline_code"
    DIFF            = "diff"
    TOOL_CALL       = "tool_call"
    TOOL_RESULT     = "tool_result"
    ERROR           = "error"
    MARKDOWN        = "markdown"
    AGENT_STATE     = "agent_state"
    PLAIN_TEXT      = "plain_text"
    UNKNOWN         = "unknown"


class RenderContext(str, Enum):
    TERMINAL        = "terminal"
    IDE             = "ide"
    API_JSON        = "api_json"
    WEB_HTML        = "web_html"
    AGENT           = "agent"


@dataclass
class Token:
    type: TokenType
    raw: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    formatter: str
    context: RenderContext
    priority: int
    trace: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    severity: str = "error"   # "warn" | "error" | "fatal"
