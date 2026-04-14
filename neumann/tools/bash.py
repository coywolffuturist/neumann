"""Bash tool — execute shell commands with sandbox restrictions.

Sandbox:
- allowed_paths: list of directories the command can access (arguments validated)
- allowed_commands: whitelist of commands (no interpreters by default)
- timeout: max execution time in seconds (default: 30)
- max_output: max bytes of output to capture (default: 65536)

Security fixes (Issue #1):
1. Command arguments validated against allowed_paths
2. Destructive commands/interpreters removed from default whitelist
3. Shell metacharacter validation prevents interpreter escape
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import Tool, ToolResult


# Safe default commands — NO interpreters, NO destructive commands
# Interpreters (python3, node, bash -c) allow arbitrary code execution
# Destructive commands (rm, mv, rmdir) can delete/overwrite files
_SAFE_DEFAULT_ALLOWED = {
    # Read-only
    "ls", "cat", "head", "tail", "wc", "find", "grep", "echo", "pwd",
    "stat", "file", "diff", "sort", "uniq", "tr", "cut", "awk", "sed",
    # Non-destructive write
    "mkdir", "touch",
    # Build/dev
    "git",
    # Info
    "whoami", "date", "uname",
}


class BashTool(Tool):
    """Execute shell commands in a sandbox."""

    def __init__(
        self,
        allowed_commands: set[str] | None = None,
        allowed_paths: list[str] | None = None,
        timeout: int = 30,
        max_output: int = 65_536,
        allow_shell_metacharacters: bool = False,
    ) -> None:
        self.allowed_commands = allowed_commands or set(_SAFE_DEFAULT_ALLOWED)
        if not allowed_paths:
            raise ValueError(
                "BashTool requires allowed_paths — no default paths for security"
            )
        self.allowed_paths = [Path(p).resolve() for p in allowed_paths]
        self.timeout = timeout
        self.max_output = max_output
        self.allow_shell_metacharacters = allow_shell_metacharacters

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Execute a shell command in a sandboxed environment."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "working_dir": {"type": "string", "description": "Working directory (must be within allowed paths)"},
            },
            "required": ["command"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        command = kwargs.get("command", "")
        working_dir = kwargs.get("working_dir")

        if not command:
            return ToolResult(self.name, error="No command provided", success=False)

        # ── Security 1: Validate base command ──────────────────────
        cmd_parts = command.split()
        if not cmd_parts:
            return ToolResult(self.name, error="Empty command", success=False)

        base_cmd = os.path.basename(cmd_parts[0])
        if base_cmd not in self.allowed_commands:
            return ToolResult(
                self.name,
                error=f"Command '{base_cmd}' not in allowed list: {sorted(self.allowed_commands)}",
                success=False,
            )

        # ── Security 2: Validate path arguments ────────────────────
        path_violation = self._validate_path_arguments(cmd_parts)
        if path_violation:
            return ToolResult(
                self.name,
                error=f"Path sandbox violation: {path_violation}",
                success=False,
            )

        # ── Security 3: Shell metacharacter validation ─────────────
        if not self.allow_shell_metacharacters:
            meta_violation = self._check_metacharacters(command)
            if meta_violation:
                return ToolResult(
                    self.name,
                    error=f"Shell metacharacters not allowed: {meta_violation}",
                    success=False,
                )

        # ── Security 4: Validate working directory ─────────────────
        if working_dir:
            wd = Path(working_dir).resolve()
            if not self._is_within_allowed_paths(wd):
                return ToolResult(
                    self.name,
                    error=f"Working directory '{working_dir}' not within allowed paths",
                    success=False,
                )
        else:
            wd = self.allowed_paths[0]

        # ── Execute ─────────────────────────────────────────────────
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(wd),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            stdout = proc.stdout[:self.max_output] if proc.stdout else ""
            stderr = proc.stderr[:self.max_output] if proc.stderr else ""

            if proc.returncode != 0:
                return ToolResult(
                    self.name,
                    output=stdout,
                    error=stderr or f"Exit code: {proc.returncode}",
                    success=False,
                    metadata={"exit_code": proc.returncode, "timeout": False},
                )

            return ToolResult(
                self.name,
                output=stdout,
                error=stderr if stderr else "",
                success=True,
                metadata={"exit_code": 0, "timeout": False},
            )

        except subprocess.TimeoutExpired:
            return ToolResult(
                self.name,
                error=f"Command timed out after {self.timeout}s",
                success=False,
                metadata={"timeout": True},
            )
        except Exception as e:
            return ToolResult(
                self.name,
                error=f"Execution error: {e}",
                success=False,
                metadata={"exception": type(e).__name__},
            )

    # ── Security helpers ────────────────────────────────────────────

    def _validate_path_arguments(self, cmd_parts: list[str]) -> str | None:
        """Validate that all path-like arguments are within allowed paths."""
        for arg in cmd_parts[1:]:
            # Skip flags
            if arg.startswith("-"):
                continue
            # Skip simple strings that aren't paths
            if not any(c in arg for c in ("/", ".")):
                continue

            # Resolve the path
            try:
                resolved = Path(arg).expanduser().resolve()
            except (OSError, ValueError):
                continue

            if not self._is_within_allowed_paths(resolved):
                return f"Argument '{arg}' resolves to '{resolved}' outside allowed paths"

        return None

    def _check_metacharacters(self, command: str) -> str | None:
        """Check for shell metacharacters that could enable code execution."""
        dangerous = ["|", ";", "`", "$(", "&&", "||", ">", ">>", "<"]
        for char in dangerous:
            if char in command:
                return f"Metacharacter '{char}' not allowed (enable allow_shell_metacharacters to allow)"
        return None

    def _is_within_allowed_paths(self, path: Path) -> bool:
        """Check if a resolved path is within any allowed path."""
        return any(str(path).startswith(str(p)) for p in self.allowed_paths)
