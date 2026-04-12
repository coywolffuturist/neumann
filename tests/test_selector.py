"""Tests for FormatSelector."""
import pytest
from neumann import TokenClassifier, ContextResolver, FormatSelector, RenderContext, TokenType


@pytest.fixture
def clf():
    return TokenClassifier()

@pytest.fixture
def sel():
    return FormatSelector()


def test_code_block_terminal(clf, sel):
    token = clf.classify("```python\nprint('hi')\n```")
    ctx = RenderContext.TERMINAL
    decision = sel.select(token, ctx)
    assert decision.formatter == "CodeBlockRenderer"


def test_tool_call_any_context(clf, sel):
    token = clf.classify('{"tool": "bash", "input": {}}')
    for ctx in RenderContext:
        decision = sel.select(token, ctx)
        assert decision.formatter == "ToolCallRenderer"


def test_error_any_context(clf, sel):
    token = clf.classify("TypeError: bad input")
    decision = sel.select(token, RenderContext.API_JSON)
    assert decision.formatter == "ErrorRenderer"


def test_fallback_for_unknown(sel):
    from neumann.types import Token, TokenType
    token = Token(type=TokenType.UNKNOWN, raw="???")
    decision = sel.select(token, RenderContext.TERMINAL)
    assert decision.formatter == "FallbackHandler"
