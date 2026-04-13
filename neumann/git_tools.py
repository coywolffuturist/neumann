"""Git Tools — safe git operations for the agent.

Provides:
- GitStatus: current branch, changed files, untracked, ahead/behind
- GitDiff: staged/unstaged/file diff as unified diff
- GitCommit: commit with message
- GitBranch: list, create, checkout branches
- GitLog: recent commit history

All operations are sandboxed to a single repository directory.

Usage:
    from neumann.git import GitTools
    git = GitTools(repo_path="/home/user/project")
    
    status = git.status()
    print(status.branch)
    print(status.changed_files)
    
    diff = git.diff()
    log = git.log(n=5)
    git.commit("fix: resolve null pointer in parser")
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GitStatus:
    """Parsed output of git status --porcelain."""
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    staged: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)
    clean: bool = True

    @property
    def has_changes(self) -> bool:
        return not self.clean


@dataclass
class GitCommit:
    """A single commit entry."""
    hash: str = ""
    short_hash: str = ""
    author: str = ""
    date: str = ""
    subject: str = ""
    body: str = ""


class GitTools:
    """Safe git operations scoped to a single repository."""

    def __init__(
        self,
        repo_path: str | Path | None = None,
        timeout: int = 30,
    ) -> None:
        self.repo = Path(repo_path or Path.cwd()).resolve()
        self.timeout = timeout
        self._validate_repo()

    def _validate_repo(self) -> None:
        if not (self.repo / ".git").exists():
            # Try to find git root
            result = self._run_git("rev-parse", "--show-toplevel", check=False)
            if result.returncode == 0:
                self.repo = Path(result.stdout.strip())
            else:
                raise ValueError(f"Not a git repository: {self.repo}")

    def _run_git(
        self, *args: str, check: bool = True, cwd: Path | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.repo),
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=check,
        )

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> GitStatus:
        """Get repository status."""
        result = GitStatus()

        # Branch info
        try:
            branch_result = self._run_git("branch", "--show-current")
            result.branch = branch_result.stdout.strip() or "HEAD (detached)"
        except Exception:
            result.branch = "unknown"

        # Staged files
        staged_result = self._run_git(
            "diff", "--cached", "--name-status", check=False
        )
        if staged_result.returncode == 0:
            for line in staged_result.stdout.strip().splitlines():
                if not line:
                    continue
                parts = line.split("\t", 1)
                status_char = parts[0][0]
                filepath = parts[1] if len(parts) > 1 else parts[0]
                if status_char == "A":
                    result.staged.append(filepath)
                elif status_char == "M":
                    result.staged.append(filepath)
                elif status_char == "D":
                    result.deleted.append(filepath)
                    result.staged.append(filepath)

        # Unstaged modified & untracked
        unstaged_result = self._run_git(
            "diff", "--name-status", check=False
        )
        if unstaged_result.returncode == 0:
            for line in unstaged_result.stdout.strip().splitlines():
                if not line:
                    continue
                parts = line.split("\t", 1)
                status_char = parts[0][0]
                filepath = parts[1] if len(parts) > 1 else parts[0]
                if status_char == "M":
                    result.modified.append(filepath)

        untracked_result = self._run_git(
            "ls-files", "--others", "--exclude-standard", check=False
        )
        if untracked_result.returncode == 0:
            result.untracked = [
                f for f in untracked_result.stdout.strip().splitlines() if f
            ]

        # Ahead/behind
        try:
            rev_result = self._run_git(
                "rev-list", "--left-right", "--count",
                "HEAD...@{upstream}",
                check=False,
            )
            if rev_result.returncode == 0:
                parts = rev_result.stdout.strip().split()
                if len(parts) == 2:
                    result.behind = int(parts[0])
                    result.ahead = int(parts[1])
        except Exception:
            pass

        result.clean = (
            not result.staged
            and not result.modified
            and not result.untracked
            and not result.deleted
        )

        return result

    # ── Diff ──────────────────────────────────────────────────────────

    def diff(self, file_path: str | None = None, staged: bool = False) -> str:
        """Get unified diff output.
        
        Args:
            file_path: specific file to diff (None = all)
            staged: show staged diff instead of unstaged
        """
        args = ["diff"]
        if staged:
            args.append("--cached")
        if file_path:
            args.append("--")
            args.append(str(file_path))

        result = self._run_git(*args, check=False)
        return result.stdout if result.returncode == 0 else ""

    def diff_stat(self, staged: bool = False) -> str:
        """Get diff --stat (summary of changes)."""
        args = ["diff", "--stat"]
        if staged:
            args.append("--cached")
        result = self._run_git(*args, check=False)
        return result.stdout if result.returncode == 0 else ""

    # ── Commit ────────────────────────────────────────────────────────

    def commit(self, message: str, amend: bool = False) -> dict[str, Any]:
        """Create a git commit."""
        args = ["commit", "-m", message]
        if amend:
            args.append("--amend")
            args.remove("-m")
            args.remove(message)
            args.extend(["-m", message, "--amend"])

        result = self._run_git(*args, check=False)
        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr.strip(),
                "hash": "",
            }

        # Get the commit hash
        hash_result = self._run_git("rev-parse", "HEAD")
        return {
            "success": True,
            "error": "",
            "hash": hash_result.stdout.strip(),
        }

    def commit_with_files(
        self, message: str, files: list[str]
    ) -> dict[str, Any]:
        """Stage specific files and commit."""
        for f in files:
            self._run_git("add", f)
        return self.commit(message)

    # ── Branch ────────────────────────────────────────────────────────

    def branches(self) -> list[str]:
        """List all local branches."""
        result = self._run_git("branch", "--format=%(refname:short)")
        return [b.strip() for b in result.stdout.strip().splitlines() if b.strip()]

    def current_branch(self) -> str:
        """Get current branch name."""
        result = self._run_git("branch", "--show-current")
        return result.stdout.strip()

    def create_branch(self, name: str, checkout: bool = True) -> bool:
        """Create a new branch."""
        if checkout:
            result = self._run_git("checkout", "-b", name, check=False)
            return result.returncode == 0
        else:
            result = self._run_git("branch", name, check=False)
            return result.returncode == 0

    def checkout_branch(self, name: str) -> bool:
        """Switch to a branch."""
        result = self._run_git("checkout", name, check=False)
        return result.returncode == 0

    # ── Log ───────────────────────────────────────────────────────────

    def log(self, n: int = 10, file_path: str | None = None) -> list[GitCommit]:
        """Get recent commits."""
        fmt = "%H|%h|%an|%ai|%s"
        args = ["log", f"-{n}", f"--format={fmt}"]
        if file_path:
            args.extend(["--", file_path])

        result = self._run_git(*args, check=False)
        if result.returncode != 0:
            return []

        commits = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|", 4)
            if len(parts) >= 5:
                commits.append(GitCommit(
                    hash=parts[0],
                    short_hash=parts[1],
                    author=parts[2],
                    date=parts[3],
                    subject=parts[4],
                ))
        return commits

    # ── Blame ─────────────────────────────────────────────────────────

    def blame(self, file_path: str) -> list[dict[str, str]]:
        """Get git blame for a file."""
        result = self._run_git(
            "blame", "--line-porcelain", str(file_path), check=False
        )
        if result.returncode != 0:
            return []

        blames = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if line.startswith("author "):
                current["author"] = line[7:]
            elif line.startswith("author-time "):
                current["time"] = line[12:]
            elif line.startswith("\t"):
                current["line"] = line[1:]
                if "author" in current:
                    blames.append(dict(current))
                    current = {}
        return blames

    # ── Remote ────────────────────────────────────────────────────────

    def remotes(self) -> list[str]:
        """List configured remotes."""
        result = self._run_git("remote")
        return [r.strip() for r in result.stdout.strip().splitlines() if r.strip()]

    def push(self, remote: str = "origin", branch: str | None = None) -> dict[str, Any]:
        """Push to remote."""
        target = branch or self.current_branch()
        result = self._run_git("push", remote, target, check=False)
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip(),
        }

    def pull(self, remote: str = "origin", branch: str | None = None) -> dict[str, Any]:
        """Pull from remote."""
        target = branch or self.current_branch()
        result = self._run_git("pull", remote, target, check=False)
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip(),
        }
