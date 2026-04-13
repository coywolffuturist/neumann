"""Write file tool — create or overwrite a file safely.

Sandbox:
- allowed_roots: list of root directories files must be under
- max_size: max content size to write (default: 1MB)
- overwrite: whether to allow overwriting existing files (default: True)

Usage:
    tool = WriteFileTool(allowed_roots=["/home/user/project"])
    result = tool.execute(file_path="/home/user/project/hello.py", content='print("hello")')
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import Tool, ToolResult


class WriteFileTool(Tool):
    """Write content to a file safely."""

    def __init__(
        self,
        allowed_roots: list[str] | None = None,
        max_size: int = 1_048_576,
        overwrite: bool = True,
        encoding: str = "utf-8",
    ) -> None:
        self.allowed_roots = [Path(r) for r in (allowed_roots or [])]
        self.max_size = max_size
        self.overwrite = overwrite
        self.encoding = encoding

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Create or overwrite a file with given content."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["file_path", "content"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        file_path = kwargs.get("file_path", "")
        content = kwargs.get("content", "")

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

        # Security: check content size
        content_bytes = content.encode(self.encoding)
        if len(content_bytes) > self.max_size:
            return ToolResult(
                self.name,
                error=f"Content too large ({len(content_bytes)} bytes, max {self.max_size})",
                success=False,
            )

        # Check overwrite policy
        if path.exists() and not self.overwrite:
            return ToolResult(self.name, error=f"File exists and overwrite=False: {file_path}", success=False)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=self.encoding)

            return ToolResult(
                self.name,
                output=f"File written: {file_path} ({len(content_bytes)} bytes)",
                success=True,
                metadata={"file_size": len(content_bytes), "created": not path.exists()},
            )
        except Exception as e:
            return ToolResult(self.name, error=f"Cannot write file: {e}", success=False)
