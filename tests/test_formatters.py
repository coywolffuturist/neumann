"""Tests for all formatters."""
import json
import pytest
from neumann import NeumannPipeline, RenderContext, TokenType
from neumann.types import Token
from neumann.registry import get_formatter


def render(raw, context_flag=None):
    env = {context_flag: True} if context_flag else {}
    return NeumannPipeline().process(raw, env=env)


# ── CodeBlockRenderer ────────────────────────────────────────

def test_code_terminal_has_border():
    r = render("```python\nprint('hi')\n```")
    assert "─" in r.rendered  # border present

def test_code_web_has_pre_tag():
    r = render("```python\nprint('hi')\n```", "is_web")
    assert "<pre>" in r.rendered
    assert 'class="language-python"' in r.rendered

def test_code_api_is_raw():
    r = render("```python\nprint('hi')\n```", "is_api")
    assert r.rendered.strip() == "print('hi')"

def test_code_no_fences_in_api():
    r = render("```\nsome code\n```", "is_api")
    assert "```" not in r.rendered


# ── DiffRenderer ─────────────────────────────────────────────

def test_diff_terminal_has_ansi():
    r = render("@@ -1,3 +1,4 @@\n+new line\n-old line\n context")
    assert "\033[" in r.rendered  # ANSI codes present

def test_diff_web_has_div():
    r = render("@@ -1,2 +1,3 @@\n+added\n-removed", "is_web")
    assert '<div class="diff">' in r.rendered
    assert 'diff-add' in r.rendered
    assert 'diff-del' in r.rendered

def test_diff_api_passthrough():
    raw = "@@ -1,2 +1,3 @@\n+added\n-removed"
    r = render(raw, "is_api")
    assert r.rendered == raw


# ── ToolCallRenderer ─────────────────────────────────────────

def test_tool_call_terminal_shows_name():
    raw = '{"tool": "bash", "input": {"command": "ls"}}'
    r = render(raw)
    assert "bash" in r.rendered

def test_tool_call_api_is_json():
    raw = '{"tool": "bash", "input": {"command": "ls"}}'
    r = render(raw, "is_api")
    parsed = json.loads(r.rendered)
    assert parsed["tool"] == "bash"

def test_tool_result_terminal():
    raw = '{"tool_result": "bash", "output": "file1.txt\nfile2.txt"}'
    r = render(raw)
    assert "bash" in r.rendered
    assert "tool_result" in r.decision.formatter.lower() or "ToolCall" in r.decision.formatter


# ── ErrorRenderer ─────────────────────────────────────────────

def test_error_terminal_has_cross():
    r = render("TypeError: unsupported operand type(s)")
    assert "✗" in r.rendered or "TypeError" in r.rendered

def test_error_web_has_div():
    r = render("ValueError: invalid literal for int()", "is_web")
    assert 'class="error-block"' in r.rendered
    assert "ValueError" in r.rendered

def test_error_api_is_json():
    r = render("TypeError: bad type", "is_api")
    parsed = json.loads(r.rendered)
    assert parsed["type"] == "TypeError"
    assert "bad type" in parsed["message"]


# ── MarkdownRenderer ─────────────────────────────────────────

def test_markdown_terminal_heading_ansi():
    r = render("## Hello World\n")
    assert "Hello World" in r.rendered
    assert "\033[" in r.rendered

def test_markdown_web_heading_html():
    r = render("## Hello World\n", "is_web")
    assert "<h2>Hello World</h2>" in r.rendered

def test_markdown_api_strips_syntax():
    r = render("## Hello **World**\n", "is_api")
    assert "#" not in r.rendered
    assert "**" not in r.rendered
    assert "Hello World" in r.rendered


# ── AgentStateRenderer ───────────────────────────────────────

def test_agent_state_terminal():
    raw = '{"type": "agent_state", "status": "running", "label": "Worker-1", "progress": 0.5}'
    r = render(raw)
    assert "Worker-1" in r.rendered
    assert "50" in r.rendered or "█" in r.rendered

def test_agent_state_web():
    raw = '{"type": "agent_state", "status": "done", "label": "Indexer", "progress": 1.0}'
    r = render(raw, "is_web")
    assert 'agent-done' in r.rendered
    assert "Indexer" in r.rendered


# ── Pipeline rendered field ──────────────────────────────────

def test_pipeline_result_has_rendered():
    r = render("Hello, world.")
    assert isinstance(r.rendered, str)
    assert len(r.rendered) > 0

def test_pipeline_timing():
    r = render("```python\npass\n```")
    assert r.duration_ms >= 0
