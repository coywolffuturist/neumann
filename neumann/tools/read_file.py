"""Read file tool — read file contents with path sandboxing.

Sandbox:
- allowed_roots: list of root directories files must be under
- max_size: max file size to read (default: 1MB)
- max_lines: max lines to return (default: 1000)
- encoding: file encoding (default: utf-8)

Usage:
    tool = ReadFileTool(allowed_roots=["/home/user/project"])
    result = tool.execute(file_path="/home/user/project/main.py")
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import Tool, ToolResult


class ReadFileTool(Tool):
    """Read a file's contents safely."""

    def __init__(
        self,
        allowed_roots: list[str] | None = None,
        max_size: int = 1_048_576,
        max_lines: int = 1000,
        encoding: str = "utf-8",
    ) -> None:
        if not allowed_roots:
            raise ValueError(
                "ReadFileTool requires allowed_roots — no default roots for security"
            )
        self.allowed_roots = [Path(r) for r in allowed_roots]
        self.max_size = max_size
        self.max_lines = max_lines
        self.encoding = encoding

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to read"},
                "offset": {"type": "integer", "description": "Starting line number (0-based)", "default": 0},
                "limit": {"type": "integer", "description": "Max lines to read", "default": 1000},
            },
            "required": ["file_path"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        file_path = kwargs.get("file_path", "")
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", self.max_lines)

        if not file_path:
            return ToolResult(self.name, error="No file path provided", success=False)

        path = Path(file_path).resolve()

        # Security: check path is within allowed roots
        if self.allowed_roots:
            if not any(str(path).startswith(str(r.resolve())) for r in self.allowed_roots):
                return ToolResult(
                    self.name,
                    error=f"File path '{file_path}' not within allowed roots",
                    success=False,
                )

        if not path.exists():
            return ToolResult(self.name, error=f"File not found: {file_path}", success=False)
        if not path.is_file():
            return ToolResult(self.name, error=f"Not a file: {file_path}", success=False)

        # Security: check file size
        try:
            size = path.stat().st_size
            if size > self.max_size:
                return ToolResult(
                    self.name,
                    error=f"File too large ({size} bytes, max {self.max_size})",
                    success=False,
                    metadata={"file_size": size},
                )
        except OSError as e:
            return ToolResult(self.name, error=f"Cannot stat file: {e}", success=False)

        try:
            with open(path, encoding=self.encoding) as f:
                lines = f.readlines()

            total_lines = len(lines)
            selected = lines[offset:offset + limit]
            content = "".join(selected)

            truncated = total_lines > offset + limit
            result = content
            if truncated:
                result += f"\n\n... ({total_lines - offset - limit} more lines)"

            return ToolResult(
                self.name,
                output=result,
                success=True,
                metadata={
                    "total_lines": total_lines,
                    "lines_read": len(selected),
                    "file_size": size,
                    "truncated": truncated,
                },
            )
        except UnicodeDecodeError as e:
            return ToolResult(self.name, error=f"File is not valid {self.encoding}: {e}", success=False)
        except Exception as e:
            return ToolResult(self.name, error=f"Cannot read file: {e}", success=False)
