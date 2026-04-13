"""Edit file tool — search & replace in a file with diff output.

Uses exact string matching: finds old_string in file, replaces with new_string.
Returns a unified diff of the change.

Sandbox:
- allowed_roots: list of root directories files must be under

Usage:
    tool = EditFileTool(allowed_roots=["/home/user/project"])
    result = tool.execute(
        file_path="/home/user/project/main.py",
        old_string='print("hello")',
        new_string='print("world")',
    )
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from . import Tool, ToolResult


class EditFileTool(Tool):
    """Search and replace text in a file."""

    def __init__(
        self,
        allowed_roots: list[str] | None = None,
        encoding: str = "utf-8",
    ) -> None:
        self.allowed_roots = [Path(r) for r in (allowed_roots or [])]
        self.encoding = encoding

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Edit a file by replacing old_string with new_string."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "old_string": {"type": "string", "description": "Text to find and replace"},
                "new_string": {"type": "string", "description": "Text to replace with"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences", "default": False},
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        file_path = kwargs.get("file_path", "")
        old_string = kwargs.get("old_string", "")
        new_string = kwargs.get("new_string", "")
        replace_all = kwargs.get("replace_all", False)

        if not file_path:
            return ToolResult(self.name, error="No file path provided", success=False)
        if not old_string:
            return ToolResult(self.name, error="No old_string provided", success=False)

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

        try:
            content = path.read_text(encoding=self.encoding)
        except Exception as e:
            return ToolResult(self.name, error=f"Cannot read file: {e}", success=False)

        # Check if old_string exists
        if old_string not in content:
            return ToolResult(
                self.name,
                error=f"String not found in file: {file_path}",
                success=False,
                metadata={"occurrences": 0},
            )

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        if new_content == content:
            return ToolResult(self.name, output="No changes made — content identical", success=True)

        # Write back
        try:
            path.write_text(new_content, encoding=self.encoding)
        except Exception as e:
            return ToolResult(self.name, error=f"Cannot write file: {e}", success=False)

        # Generate diff
        diff = _generate_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            from_file=str(path),
        )

        occurrences = content.count(old_string)
        replaced = 1 if not replace_all else occurrences

        return ToolResult(
            self.name,
            output=f"Replaced {replaced} occurrence(s) in {file_path}\n\n{diff}",
            success=True,
            metadata={"replacements": replaced, "total_occurrences": occurrences},
        )


def _generate_diff(old_lines: list[str], new_lines: list[str], from_file: str = "file") -> str:
    """Generate a unified diff string."""
    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{from_file}",
        tofile=f"b/{from_file}",
        n=3,
    ))
    return "".join(diff_lines)
