"""Bash tool — execute shell commands with sandbox restrictions.

Sandbox:
- allowed_paths: list of directories the command can access
- allowed_commands: whitelist of commands (default: common safe commands)
- timeout: max execution time in seconds (default: 30)
- max_output: max bytes of output to capture (default: 65536)

Usage:
    tool = BashTool(allowed_paths=["/home/user/project"])
    result = tool.execute(command="ls -la /home/user/project")
"""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path
from typing import Any

from . import Tool, ToolResult


# Default allowed commands (whitelist approach)
_DEFAULT_ALLOWED = {
    "ls", "cat", "head", "tail", "wc", "find", "grep", "echo", "pwd",
    "stat", "file", "diff", "sort", "uniq", "tr", "cut", "awk", "sed",
    "mkdir", "touch", "cp", "mv", "rm", "rmdir",
    "python", "python3", "node", "npm", "pip", "pip3",
    "git",
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
    ) -> None:
        self.allowed_commands = allowed_commands or set(_DEFAULT_ALLOWED)
        self.allowed_paths = [Path(p) for p in (allowed_paths or [os.getcwd()])]
        self.timeout = timeout
        self.max_output = max_output

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

        # ── Security: validate command ──────────────────────────────
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

        # ── Security: validate working directory ────────────────────
        if working_dir:
            wd = Path(working_dir).resolve()
            if not any(str(wd).startswith(str(p.resolve())) for p in self.allowed_paths):
                return ToolResult(
                    self.name,
                    error=f"Working directory '{working_dir}' not within allowed paths",
                    success=False,
                )
        else:
            wd = self.allowed_paths[0].resolve()

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
