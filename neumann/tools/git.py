"""Git tool — wraps GitTools as a Neumann tool for agent execution.

The agent can call this tool to perform git operations:
- status, diff, log, branch management
- commit, push, pull

Usage (via pipeline):
    pipeline.execute_tool_call(
        '{"tool": "git", "input": {"action": "status"}}'
    )
    pipeline.execute_tool_call(
        '{"tool": "git", "input": {"action": "commit", "message": "fix: bug"}}'
    )
"""
from __future__ import annotations

import json
from typing import Any

from . import Tool, ToolResult
from ..git_tools import GitTools


class GitTool(Tool):
    """Git operations as a Neumann tool."""

    def __init__(
        self,
        repo_path: str | None = None,
        timeout: int = 30,
    ) -> None:
        self._git = GitTools(repo_path=repo_path, timeout=timeout)

    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return "Perform git operations (status, diff, commit, branch, log, push, pull)"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "diff", "diff_stat", "commit", "log",
                             "branches", "current_branch", "create_branch",
                             "checkout", "blame", "remotes", "push", "pull"],
                    "description": "Git action to perform",
                },
                "message": {"type": "string", "description": "Commit message"},
                "file_path": {"type": "string", "description": "File path for diff/blame"},
                "branch": {"type": "string", "description": "Branch name"},
                "staged": {"type": "boolean", "description": "Show staged diff"},
                "n": {"type": "integer", "description": "Number of log entries", "default": 10},
                "remote": {"type": "string", "description": "Remote name", "default": "origin"},
            },
            "required": ["action"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        git = self._git

        try:
            if action == "status":
                status = git.status()
                output = self._format_status(status)
                return ToolResult(self.name, output=output, success=True)

            elif action == "diff":
                diff = git.diff(
                    file_path=kwargs.get("file_path"),
                    staged=kwargs.get("staged", False),
                )
                return ToolResult(
                    self.name,
                    output=diff or "No changes.",
                    success=True,
                )

            elif action == "diff_stat":
                stat = git.diff_stat(staged=kwargs.get("staged", False))
                return ToolResult(self.name, output=stat or "No changes.", success=True)

            elif action == "commit":
                message = kwargs.get("message", "")
                if not message:
                    return ToolResult(self.name, error="No commit message provided", success=False)
                result = git.commit(message)
                if result["success"]:
                    return ToolResult(
                        self.name,
                        output=f"Committed: {result['hash'][:8]}\n{message}",
                        success=True,
                    )
                return ToolResult(self.name, error=result["error"], success=False)

            elif action == "log":
                n = kwargs.get("n", 10)
                commits = git.log(n=n, file_path=kwargs.get("file_path"))
                if not commits:
                    return ToolResult(self.name, output="No commits found.", success=True)
                lines = []
                for c in commits:
                    lines.append(f"{c.short_hash} {c.date[:10]} {c.author}: {c.subject}")
                return ToolResult(self.name, output="\n".join(lines), success=True)

            elif action == "branches":
                branches = git.branches()
                current = git.current_branch()
                lines = []
                for b in branches:
                    marker = "* " if b == current else "  "
                    lines.append(f"{marker}{b}")
                return ToolResult(self.name, output="\n".join(lines), success=True)

            elif action == "current_branch":
                return ToolResult(
                    self.name,
                    output=git.current_branch(),
                    success=True,
                )

            elif action == "create_branch":
                branch = kwargs.get("branch", "")
                if not branch:
                    return ToolResult(self.name, error="No branch name provided", success=False)
                ok = git.create_branch(branch)
                if ok:
                    return ToolResult(
                        self.name,
                        output=f"Created and switched to branch: {branch}",
                        success=True,
                    )
                return ToolResult(self.name, error=f"Failed to create branch: {branch}", success=False)

            elif action == "checkout":
                branch = kwargs.get("branch", "")
                if not branch:
                    return ToolResult(self.name, error="No branch name provided", success=False)
                ok = git.checkout_branch(branch)
                if ok:
                    return ToolResult(
                        self.name,
                        output=f"Switched to branch: {branch}",
                        success=True,
                    )
                return ToolResult(self.name, error=f"Failed to checkout branch: {branch}", success=False)

            elif action == "blame":
                file_path = kwargs.get("file_path", "")
                if not file_path:
                    return ToolResult(self.name, error="No file path provided", success=False)
                blames = git.blame(file_path)
                lines = [f"{b.get('author', '?'):20s} {b.get('time', '')}: {b.get('line', '')}" for b in blames]
                return ToolResult(self.name, output="\n".join(lines), success=True)

            elif action == "remotes":
                remotes = git.remotes()
                return ToolResult(self.name, output="\n".join(remotes) or "No remotes configured.", success=True)

            elif action == "push":
                remote = kwargs.get("remote", "origin")
                branch = kwargs.get("branch")
                result = git.push(remote=remote, branch=branch)
                if result["success"]:
                    return ToolResult(self.name, output=result["output"] or "Pushed successfully.", success=True)
                return ToolResult(self.name, error=result["error"], success=False)

            elif action == "pull":
                remote = kwargs.get("remote", "origin")
                branch = kwargs.get("branch")
                result = git.pull(remote=remote, branch=branch)
                if result["success"]:
                    return ToolResult(self.name, output=result["output"] or "Pulled successfully.", success=True)
                return ToolResult(self.name, error=result["error"], success=False)

            else:
                return ToolResult(
                    self.name,
                    error=f"Unknown git action: {action}. Available: status, diff, diff_stat, commit, log, branches, current_branch, create_branch, checkout, blame, remotes, push, pull",
                    success=False,
                )

        except Exception as e:
            return ToolResult(self.name, error=f"Git error: {e}", success=False)

    @staticmethod
    def _format_status(status) -> str:
        lines = []
        lines.append(f"Branch: {status.branch}")
        if status.ahead > 0:
            lines.append(f"  Ahead of origin by {status.ahead} commit(s)")
        if status.behind > 0:
            lines.append(f"  Behind origin by {status.behind} commit(s)")
        lines.append("")

        if status.staged:
            lines.append("Staged changes:")
            for f in status.staged:
                lines.append(f"  {f}")
            lines.append("")

        if status.modified:
            lines.append("Modified (not staged):")
            for f in status.modified:
                lines.append(f"  {f}")
            lines.append("")

        if status.untracked:
            lines.append("Untracked files:")
            for f in status.untracked:
                lines.append(f"  {f}")
            lines.append("")

        if status.deleted:
            lines.append("Deleted:")
            for f in status.deleted:
                lines.append(f"  {f}")
            lines.append("")

        if status.clean:
            lines.append("Working tree clean.")

        return "\n".join(lines)
