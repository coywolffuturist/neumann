"""Tests for Git tools and GitTool."""
import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from neumann import GitTools, GitStatus
from neumann.tools.git import GitTool


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary git repository with some commits."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    # Init repo
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=repo, check=True, capture_output=True)
    # Create a file and commit it
    (repo / "hello.py").write_text('print("hello")\n')
    subprocess.run(["git", "add", "hello.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"],
                   cwd=repo, check=True, capture_output=True)
    # Make another change (uncommitted)
    (repo / "hello.py").write_text('print("world")\n')
    # Add an untracked file
    (repo / "new_file.py").write_text('# new file\n')
    return repo


class TestGitTools:
    def test_init_valid_repo(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        assert git.repo == tmp_repo

    def test_init_invalid_repo(self, tmp_path):
        with pytest.raises(ValueError, match="Not a git repository"):
            GitTools(repo_path=str(tmp_path))

    def test_status(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        status = git.status()
        assert isinstance(status, GitStatus)
        assert status.modified  # hello.py was modified

    def test_status_clean(self, tmp_repo):
        # Stage the changes
        subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "update"],
                       cwd=tmp_repo, check=True, capture_output=True)
        git = GitTools(repo_path=str(tmp_repo))
        status = git.status()
        assert status.clean

    def test_diff(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        diff = git.diff()
        assert "print" in diff  # diff should contain the changed line

    def test_diff_specific_file(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        diff = git.diff(file_path="hello.py")
        assert "hello" in diff or "world" in diff

    def test_diff_stat(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        stat = git.diff_stat()
        assert "hello.py" in stat

    def test_log(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        commits = git.log(n=5)
        assert len(commits) >= 1
        assert commits[0].subject == "initial commit"
        assert commits[0].short_hash

    def test_current_branch(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        branch = git.current_branch()
        # Default branch name varies by git version
        assert branch in ("master", "main", "")

    def test_branches(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        branches = git.branches()
        assert len(branches) >= 1

    def test_create_and_checkout_branch(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        ok = git.create_branch("feature", checkout=False)
        assert ok
        assert "feature" in git.branches()

        ok = git.checkout_branch("feature")
        assert ok
        assert git.current_branch() == "feature"

    def test_commit(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        # Stage the changes first
        subprocess.run(["git", "add", "hello.py"], cwd=tmp_repo, check=True, capture_output=True)
        result = git.commit("update hello")
        assert result["success"]
        assert result["hash"]

    def test_commit_no_staged_files(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        # hello.py is modified but not staged
        result = git.commit("update hello")
        # Should fail gracefully since nothing is staged
        assert not result["success"] or "nothing" in result.get("error", "").lower()

    def test_commit_with_files(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        result = git.commit_with_files("update hello", ["hello.py"])
        assert result["success"]

    def test_blame(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        blames = git.blame("hello.py")
        assert len(blames) >= 1
        # The modified line might show "Not Committed Yet" which is expected
        author = blames[0].get("author", "")
        assert author in ("Test User", "Not Committed Yet")

    def test_remotes(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        remotes = git.remotes()
        assert remotes == []  # No remotes configured

    def test_push_no_remote(self, tmp_repo):
        git = GitTools(repo_path=str(tmp_repo))
        result = git.push()
        assert not result["success"]


class TestGitTool:
    def test_status_action(self, tmp_repo):
        tool = GitTool(repo_path=str(tmp_repo))
        result = tool.execute(action="status")
        assert result.success
        assert "Branch:" in result.output

    def test_diff_action(self, tmp_repo):
        tool = GitTool(repo_path=str(tmp_repo))
        result = tool.execute(action="diff")
        assert result.success

    def test_diff_stat_action(self, tmp_repo):
        tool = GitTool(repo_path=str(tmp_repo))
        result = tool.execute(action="diff_stat")
        assert result.success

    def test_log_action(self, tmp_repo):
        tool = GitTool(repo_path=str(tmp_repo))
        result = tool.execute(action="log", n=5)
        assert result.success
        assert "initial commit" in result.output

    def test_current_branch_action(self, tmp_repo):
        tool = GitTool(repo_path=str(tmp_repo))
        result = tool.execute(action="current_branch")
        assert result.success

    def test_branches_action(self, tmp_repo):
        tool = GitTool(repo_path=str(tmp_repo))
        result = tool.execute(action="branches")
        assert result.success

    def test_unknown_action(self):
        tool = GitTool()
        result = tool.execute(action="unknown_action")
        assert not result.success
        assert "Unknown git action" in result.error

    def test_no_action(self):
        tool = GitTool()
        result = tool.execute()
        assert not result.success


class TestPipelineGitTool:
    def test_execute_via_pipeline(self, tmp_repo):
        from neumann import NeumannPipeline
        from neumann.tools.registry import register_defaults

        register_defaults()
        pipeline = NeumannPipeline()
        result = pipeline.execute_tool_call(
            json.dumps({
                "tool": "git",
                "input": {"action": "log", "n": 3},
            })
        )
        assert result.validation.valid
        # Check that git tool result is in output (rendered has ANSI codes)
        assert "git" in result.rendered.lower() or "initial" in result.decision.trace[0]
