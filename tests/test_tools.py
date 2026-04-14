"""Tests for Tool Engine: tools, registry, and pipeline integration."""
import json
import os
import tempfile
from pathlib import Path

import pytest
from neumann import NeumannPipeline, Token, TokenType, ToolResult
from neumann.tools import Tool
from neumann.tools.registry import (
    ToolResult as TR,
    register_tool, unregister_tool, get_tool,
    list_tools, execute_tool, register_defaults,
    _REGISTRY,
)


# ── Tool base & registry ────────────────────────────────────────

class TestToolRegistry:
    def setup_method(self):
        self._saved = dict(_REGISTRY)
        _REGISTRY.clear()

    def teardown_method(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._saved)

    def test_register_and_get(self):
        class DummyTool(Tool):
            @property
            def name(self): return "dummy"
            def execute(self, **kwargs): return ToolResult("dummy", output="ok")

        register_tool(DummyTool())
        tool = get_tool("dummy")
        assert tool is not None
        assert tool.name == "dummy"

    def test_execute_tool(self):
        class DummyTool(Tool):
            @property
            def name(self): return "dummy"
            def execute(self, **kwargs): return ToolResult("dummy", output="ok")

        register_tool(DummyTool())
        result = execute_tool("dummy")
        assert result.success
        assert result.output == "ok"

    def test_execute_unknown_tool(self):
        result = execute_tool("nonexistent")
        assert not result.success
        assert "Unknown tool" in result.error

    def test_list_tools(self):
        register_defaults()
        tools = list_tools()
        assert "bash" in tools
        assert "read_file" in tools
        assert "write_file" in tools


# ── Bash tool ───────────────────────────────────────────────────

class TestBashTool:
    def test_ls_command(self):
        from neumann.tools.bash import BashTool
        tool = BashTool(allowed_paths=[os.getcwd()])
        result = tool.execute(command="ls")
        assert result.success
        assert result.metadata["exit_code"] == 0

    def test_echo_command(self):
        from neumann.tools.bash import BashTool
        tool = BashTool(allowed_paths=[os.getcwd()])
        result = tool.execute(command='echo hello')
        assert result.success
        assert "hello" in result.output

    def test_disallowed_command(self):
        from neumann.tools.bash import BashTool
        tool = BashTool(allowed_commands={"ls"}, allowed_paths=[os.getcwd()])
        result = tool.execute(command="rm -rf /")
        assert not result.success
        assert "not in allowed" in result.error

    def test_empty_command(self):
        from neumann.tools.bash import BashTool
        tool = BashTool(allowed_paths=[os.getcwd()])
        result = tool.execute(command="")
        assert not result.success


# ── Read file tool ──────────────────────────────────────────────

class TestReadFileTool:
    def test_read_file(self, tmp_path):
        from neumann.tools.read_file import ReadFileTool
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        tool = ReadFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(file_path=str(f))
        assert result.success
        assert "hello world" in result.output

    def test_file_not_found(self, tmp_path):
        from neumann.tools.read_file import ReadFileTool
        tool = ReadFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(file_path=str(tmp_path / "nope.txt"))
        assert not result.success
        assert "not found" in result.error

    def test_path_sandbox(self, tmp_path):
        from neumann.tools.read_file import ReadFileTool
        tool = ReadFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(file_path="/etc/passwd")
        assert not result.success
        assert "not within" in result.error


# ── Write file tool ─────────────────────────────────────────────

class TestWriteFileTool:
    def test_write_file(self, tmp_path):
        from neumann.tools.write_file import WriteFileTool
        tool = WriteFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(file_path=str(tmp_path / "out.txt"), content="hello")
        assert result.success
        assert (tmp_path / "out.txt").read_text() == "hello"

    def test_no_path(self, tmp_path):
        from neumann.tools.write_file import WriteFileTool
        tool = WriteFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(content="hello")
        assert not result.success

    def test_path_sandbox(self, tmp_path):
        from neumann.tools.write_file import WriteFileTool
        tool = WriteFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(file_path="/tmp/out.txt", content="hello")
        assert not result.success


# ── Edit file tool ──────────────────────────────────────────────

class TestEditFileTool:
    def test_simple_replace(self, tmp_path):
        from neumann.tools.edit_file import EditFileTool
        f = tmp_path / "code.py"
        f.write_text('print("hello")\n')
        tool = EditFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(
            file_path=str(f),
            old_string='print("hello")',
            new_string='print("world")',
        )
        assert result.success
        assert f.read_text() == 'print("world")\n'

    def test_string_not_found(self, tmp_path):
        from neumann.tools.edit_file import EditFileTool
        f = tmp_path / "code.py"
        f.write_text("no match here")
        tool = EditFileTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(file_path=str(f), old_string="xyz", new_string="abc")
        assert not result.success
        assert "not found" in result.error

    def test_generate_diff(self):
        from neumann.tools.edit_file import _generate_diff
        diff = _generate_diff(["hello\n"], ["world\n"], "test.py")
        assert "-hello" in diff
        assert "+world" in diff


# ── Grep tool ───────────────────────────────────────────────────

class TestGrepTool:
    def test_find_pattern(self, tmp_path):
        from neumann.tools.grep import GrepTool
        f = tmp_path / "search.py"
        f.write_text("def main():\n    pass\n")
        tool = GrepTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(pattern="def main", file_path=str(f))
        assert result.success
        assert result.metadata["matches"] == 1

    def test_no_match(self, tmp_path):
        from neumann.tools.grep import GrepTool
        f = tmp_path / "search.py"
        f.write_text("no match\n")
        tool = GrepTool(allowed_roots=[str(tmp_path)])
        result = tool.execute(pattern="def main", file_path=str(f))
        assert result.success
        assert result.metadata["matches"] == 0


# ── Pipeline tool execution ─────────────────────────────────────

class TestPipelineToolExecution:
    def test_execute_tool_call(self):
        register_defaults()
        pipeline = NeumannPipeline()
        result = pipeline.execute_tool_call(
            '{"tool": "bash", "input": {"command": "echo hello"}}'
        )
        assert result.token.type == TokenType.TOOL_RESULT
        assert result.validation.valid

    def test_execute_tool_call_invalid_json(self):
        pipeline = NeumannPipeline()
        result = pipeline.execute_tool_call("not json")
        assert result.token.type == TokenType.ERROR
        assert "Invalid JSON" in result.decision.trace[0]

    def test_execute_tool_call_no_tool_name(self):
        pipeline = NeumannPipeline()
        result = pipeline.execute_tool_call('{"input": {}}')
        assert result.token.type == TokenType.ERROR

    def test_execute_tool_call_unknown_tool(self):
        register_defaults()
        pipeline = NeumannPipeline()
        result = pipeline.execute_tool_call(
            '{"tool": "nonexistent", "input": {}}'
        )
        assert result.token.type == TokenType.ERROR
