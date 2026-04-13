"""Tool Engine — executable tools for Neumann agent.

Architecture:
- Each tool is a class with an `execute()` method
- Tools are registered in a registry (like formatters)
- Tool calls come in as JSON: {"tool": "name", "input": {...}}
- Tool returns structured result: {"tool_result": "name", "output": ..., "error": ...}

Design principles:
- Pure execution: same input → same output (for deterministic tools)
- Sandboxed: file operations restricted to allowed paths
- Observable: every execution logged with timing & result
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result from a tool execution."""
    tool_name: str
    output: str = ""
    error: str = ""
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "tool_result": self.tool_name,
            "success": self.success,
        }
        if self.error:
            d["error"] = self.error
        if self.output:
            d["output"] = self.output
        d.update(self.metadata)
        return d

    def to_json_str(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), indent=indent)


class Tool(ABC):
    """Base class for all Neumann tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name (e.g. 'bash', 'read_file', 'write_file')."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description."""
        return ""

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema of input parameters."""
        return {"type": "object", "properties": {}}

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with given parameters."""
        ...
