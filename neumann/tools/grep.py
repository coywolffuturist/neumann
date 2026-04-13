"""Grep tool — search for patterns in files.

Sandbox:
- allowed_roots: list of root directories to search in
- max_files: max number of files to scan (default: 1000)
- max_results: max results to return (default: 100)

Usage:
    tool = GrepTool(allowed_roots=["/home/user/project"])
    result = tool.execute(pattern="def main", glob="*.py")
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from . import Tool, ToolResult


class GrepTool(Tool):
    """Search for text patterns in files."""

    def __init__(
        self,
        allowed_roots: list[str] | None = None,
        max_files: int = 1000,
        max_results: int = 100,
        encoding: str = "utf-8",
    ) -> None:
        self.allowed_roots = [Path(r) for r in (allowed_roots or [])]
        self.max_files = max_files
        self.max_results = max_results
        self.encoding = encoding

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "Search for a pattern in files within allowed roots."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text pattern to search for"},
                "glob": {"type": "string", "description": "File glob pattern (e.g. '*.py')", "default": "*"},
                "file_path": {"type": "string", "description": "Specific file to search (overrides glob)"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search", "default": True},
            },
            "required": ["pattern"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        pattern = kwargs.get("pattern", "")
        glob_pat = kwargs.get("glob", "*")
        specific_file = kwargs.get("file_path")
        case_sensitive = kwargs.get("case_sensitive", True)

        if not pattern:
            return ToolResult(self.name, error="No search pattern provided", success=False)

        results: list[dict[str, Any]] = []
        files_to_search: list[Path] = []

        # Determine which files to search
        if specific_file:
            path = Path(specific_file).resolve()
            if self.allowed_roots and not any(str(path).startswith(str(r.resolve())) for r in self.allowed_roots):
                return ToolResult(self.name, error=f"File not within allowed roots", success=False)
            if path.exists() and path.is_file():
                files_to_search.append(path)
        elif self.allowed_roots:
            for root in self.allowed_roots:
                if root.is_dir():
                    for f in root.rglob(glob_pat):
                        if f.is_file() and not self._is_binary(f):
                            files_to_search.append(f)
                            if len(files_to_search) >= self.max_files:
                                break
                if len(files_to_search) >= self.max_files:
                    break
        else:
            return ToolResult(self.name, error="No allowed_roots configured and no specific file given", success=False)

        # Search
        search_pattern = pattern if case_sensitive else pattern.lower()
        for file_path in files_to_search:
            try:
                content = file_path.read_text(encoding=self.encoding)
                lines = content.splitlines()
                for i, line in enumerate(lines, 1):
                    check_line = line if case_sensitive else line.lower()
                    if search_pattern in check_line:
                        results.append({
                            "file": str(file_path),
                            "line": i,
                            "text": line.strip(),
                        })
                        if len(results) >= self.max_results:
                            return self._format_results(results, pattern, file_path=str(file_path), truncated=True)
            except (UnicodeDecodeError, PermissionError, OSError):
                continue

        return self._format_results(results, pattern, truncated=False)

    def _format_results(
        self,
        results: list[dict[str, Any]],
        pattern: str,
        file_path: str = "",
        truncated: bool = False,
    ) -> ToolResult:
        if not results:
            return ToolResult(
                self.name,
                output=f"No matches found for pattern: {pattern}",
                success=True,
                metadata={"matches": 0},
            )

        lines = [f"Found {len(results)} match(es) for '{pattern}':", ""]
        for r in results:
            lines.append(f"{r['file']}:{r['line']}: {r['text']}")
        if truncated:
            lines.append(f"\n... (truncated at {self.max_results} results)")

        return ToolResult(
            self.name,
            output="\n".join(lines),
            success=True,
            metadata={"matches": len(results), "truncated": truncated},
        )

    @staticmethod
    def _is_binary(path: Path) -> bool:
        """Heuristic: skip files with null bytes in first 8KB."""
        try:
            chunk = path.read_bytes()[:8192]
            return b'\x00' in chunk
        except (OSError, PermissionError):
            return True
