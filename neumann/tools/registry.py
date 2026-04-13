"""ToolRegistry — maps tool names to instances and handles execution.

Usage:
    from neumann.tools.registry import ToolRegistry, register_defaults
    registry = ToolRegistry()
    register_defaults(registry)
    result = registry.execute("bash", command="ls -la")
"""
from __future__ import annotations

import json
from typing import Any

from . import Tool, ToolResult

_REGISTRY: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    """Register a tool instance."""
    _REGISTRY[tool.name] = tool


def unregister_tool(name: str) -> None:
    """Remove a tool from the registry."""
    _REGISTRY.pop(name, None)


def get_tool(name: str) -> Tool | None:
    """Get a tool by name."""
    return _REGISTRY.get(name)


def list_tools() -> dict[str, dict[str, Any]]:
    """List all registered tools with their metadata."""
    return {
        name: {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for name, tool in _REGISTRY.items()
    }


def execute_tool(name: str, **kwargs: Any) -> ToolResult:
    """Execute a tool by name with given parameters."""
    tool = _REGISTRY.get(name)
    if not tool:
        return ToolResult(
            tool_name=name,
            error=f"Unknown tool: {name}. Available: {sorted(_REGISTRY.keys())}",
            success=False,
        )
    try:
        return tool.execute(**kwargs)
    except Exception as e:
        return ToolResult(
            tool_name=name,
            error=f"Tool execution error: {e}",
            success=False,
        )


def register_defaults() -> None:
    """Register the default tool set."""
    from .bash import BashTool
    from .read_file import ReadFileTool
    from .write_file import WriteFileTool
    from .edit_file import EditFileTool
    from .grep import GrepTool
    from .git import GitTool

    _REGISTRY.clear()
    _REGISTRY.update({
        "bash":       BashTool(),
        "read_file":  ReadFileTool(),
        "write_file": WriteFileTool(),
        "edit_file":  EditFileTool(),
        "grep":       GrepTool(),
        "git":        GitTool(),
    })
