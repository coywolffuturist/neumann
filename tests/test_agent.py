"""Tests for autonomous Agent Loop."""
import json
import subprocess
from pathlib import Path

import pytest
from neumann import NeumannAgent, AgentConfig, AgentResult, AgentStatus, SubTask, TaskPlan


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temp git repo with a Python file."""
    repo = tmp_path / "agent_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=repo, check=True, capture_output=True)
    (repo / "main.py").write_text('def hello():\n    print("hello")\n')
    subprocess.run(["git", "add", "main.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


# ═══════════════════════════════════════════════════════════════════
# Core Types
# ═══════════════════════════════════════════════════════════════════

class TestSubTask:
    def test_defaults(self):
        st = SubTask(id=1, description="test")
        assert st.status == "pending"
        assert st.retries == 0
        assert st.max_retries == 2
        assert st.tool == ""

    def test_to_dict(self):
        st = SubTask(id=1, description="read file", tool="read_file")
        d = st.to_dict()
        assert d["id"] == 1
        assert d["tool"] == "read_file"


class TestTaskPlan:
    def test_empty_plan(self):
        plan = TaskPlan()
        assert plan.is_complete
        assert plan.progress == 0.0
        assert plan.next_pending() is None

    def test_plan_with_subtasks(self):
        plan = TaskPlan(subtasks=[
            SubTask(id=1, description="step 1"),
            SubTask(id=2, description="step 2"),
        ], total=2)
        assert not plan.is_complete
        assert plan.progress == 0.0
        assert plan.next_pending().id == 1

    def test_plan_completion(self):
        plan = TaskPlan(subtasks=[
            SubTask(id=1, description="step 1"),
            SubTask(id=2, description="step 2"),
        ], total=2)
        plan.completed = 2
        assert plan.is_complete
        assert plan.progress == 1.0

    def test_partial_completion(self):
        plan = TaskPlan(subtasks=[
            SubTask(id=1, description="step 1", status="done"),
            SubTask(id=2, description="step 2"),
        ], total=2, completed=1)
        assert not plan.is_complete
        assert plan.next_pending().id == 2

    def test_to_dict(self):
        plan = TaskPlan(summary="test plan", total=3, completed=1)
        d = plan.to_dict()
        assert d["summary"] == "test plan"
        assert d["completed"] == 1
        assert d["progress"] == pytest.approx(1/3, abs=0.01)


class TestAgentResult:
    def test_result_fields(self):
        result = AgentResult(task="test", status="done", output="works")
        assert result.task == "test"
        assert result.status == "done"
        assert result.output == "works"
        assert result.errors == []
        assert result.iterations == 0


# ═══════════════════════════════════════════════════════════════════
# Agent Initialization
# ═══════════════════════════════════════════════════════════════════

class TestAgentInit:
    def test_default_config(self):
        agent = NeumannAgent()
        assert agent.config.model == "gpt-4o"
        assert agent.config.max_iterations == 50
        assert agent.config.require_confirmation is False

    def test_custom_config(self):
        cfg = AgentConfig(model="claude-sonnet-4-20250514", max_iterations=10)
        agent = NeumannAgent(config=cfg)
        assert agent.config.model == "claude-sonnet-4-20250514"
        assert agent.config.max_iterations == 10

    def test_repo_path_sets_git(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        assert agent.git is not None

    def test_invalid_repo_path(self, tmp_path):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_path / "not_a_repo")))
        assert agent.git is None  # Should gracefully handle non-repo

    def test_status_starts_waiting(self):
        agent = NeumannAgent()
        assert agent.status == AgentStatus.WAITING


# ═══════════════════════════════════════════════════════════════════
# Agent Run — Rule-Based Planning
# ═══════════════════════════════════════════════════════════════════

class TestAgentRun:
    def test_run_git_status(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("git status")
        assert result.status in ("done", "partial")
        assert result.plan.total >= 1
        # Should have a git status subtask
        tools_used = [st.tool for st in result.plan.subtasks]
        assert "git" in tools_used

    def test_run_git_log(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("show git log")
        assert result.plan.total >= 1
        tools_used = [st.tool for st in result.plan.subtasks]
        assert "git" in tools_used

    def test_run_git_branches(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("list branches")
        assert result.plan.total >= 1
        tools_used = [st.tool for st in result.plan.subtasks]
        assert "git" in tools_used

    def test_run_commit(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run('commit "update code"')
        assert result.plan.total >= 1
        # Should have status check + commit
        tasks = [st.description.lower() for st in result.plan.subtasks]
        assert any("status" in t for t in tasks)
        assert any("commit" in t for t in tasks)

    def test_run_search(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("search for def hello")
        assert result.plan.total >= 1
        tools_used = [st.tool for st in result.plan.subtasks]
        assert "grep" in tools_used

    def test_run_run_command(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("run echo hello")
        assert result.plan.total >= 1
        tools_used = [st.tool for st in result.plan.subtasks]
        assert "bash" in tools_used

    def test_run_edit_file(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("fix the bug in main.py")
        assert result.plan.total >= 1
        tools_used = [st.tool for st in result.plan.subtasks]
        assert "read_file" in tools_used

    def test_run_create_file(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("create a new file test.py")
        assert result.plan.total >= 1
        tools_used = [st.tool for st in result.plan.subtasks]
        assert "write_file" in tools_used

    def test_run_generic_task(self):
        agent = NeumannAgent()
        result = agent.run("hello, can you help me?")
        # No specific pattern matched — direct answer mode
        assert result.status == "done"


# ═══════════════════════════════════════════════════════════════════
# Agent Execution
# ═══════════════════════════════════════════════════════════════════

class TestAgentExecution:
    def test_execute_subtask_read_file(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        st = SubTask(id=1, description="read main", tool="read_file",
                     tool_input={"file_path": str(tmp_repo / "main.py")})
        result = agent._execute_subtask(st)
        assert result["success"]
        assert "hello" in result["output"]

    def test_execute_subtask_git_status(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        st = SubTask(id=1, description="status", tool="git",
                     tool_input={"action": "status"})
        result = agent._execute_subtask(st)
        assert result["success"]

    def test_execute_subtask_disallowed_tool(self):
        agent = NeumannAgent(config=AgentConfig(allowed_tools={"read_file"}))
        st = SubTask(id=1, description="bash", tool="bash",
                     tool_input={"command": "ls"})
        result = agent._execute_subtask(st)
        assert not result["success"]
        assert "not in allowed" in result["error"]

    def test_execute_subtask_no_tool(self):
        agent = NeumannAgent()
        st = SubTask(id=1, description="nothing", tool="")
        result = agent._execute_subtask(st)
        assert not result["success"]


# ═══════════════════════════════════════════════════════════════════
# Self-Correction
# ═══════════════════════════════════════════════════════════════════

class TestSelfCorrection:
    def test_retry_on_generic_error(self):
        agent = NeumannAgent()
        st = SubTask(id=1, description="test", tool="bash",
                     tool_input={"command": "echo hi"}, max_retries=2)
        result = agent._self_correct(st, "some error occurred")
        assert result["action"] == "retry"

    def test_skip_on_file_not_found(self):
        agent = NeumannAgent()
        st = SubTask(id=1, description="read", tool="read_file",
                     tool_input={"file_path": "missing.py"})
        result = agent._self_correct(st, "File not found: missing.py")
        assert result["action"] == "skip"

    def test_skip_on_string_not_found(self):
        agent = NeumannAgent()
        st = SubTask(id=1, description="edit", tool="edit_file",
                     tool_input={"file_path": "main.py"})
        result = agent._self_correct(st, "String not found in file")
        assert result["action"] == "skip"

    def test_skip_on_command_not_allowed(self):
        agent = NeumannAgent()
        st = SubTask(id=1, description="cmd", tool="bash")
        result = agent._self_correct(st, "Command 'rm' not in allowed list")
        assert result["action"] == "skip"


# ═══════════════════════════════════════════════════════════════════
# Introspection
# ═══════════════════════════════════════════════════════════════════

class TestAgentIntrospection:
    def test_stats(self):
        agent = NeumannAgent()
        stats = agent.get_stats()
        assert "status" in stats
        assert "iteration" in stats
        assert "plan" in stats
        assert "tool_calls" in stats
        assert "memory" in stats
        assert "logger" in stats

    def test_reset(self):
        agent = NeumannAgent()
        agent.run("hello")  # Sets some state
        agent.reset()
        assert agent.status == AgentStatus.WAITING
        assert agent.current_plan is None
        assert agent._iteration_count == 0

    def test_output_format(self, tmp_repo):
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("git status")
        assert "Task:" in result.output
        assert "Results:" in result.output


# ═══════════════════════════════════════════════════════════════════
# Full Integration — Real git repo
# ═══════════════════════════════════════════════════════════════════

class TestAgentIntegration:
    def test_status_then_commit_flow(self, tmp_repo):
        """Agent can check status and commit."""
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))

        # 1. Check status
        result1 = agent.run("git status")
        assert result1.status in ("done", "partial")

        # 2. Agent can handle multiple tasks sequentially
        result2 = agent.run("show git log")
        assert result2.status in ("done", "partial")

    def test_search_and_read_flow(self, tmp_repo):
        """Agent can search and read files."""
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))

        result = agent.run("search for def hello")
        assert result.status in ("done", "partial")

    def test_output_is_human_readable(self, tmp_repo):
        """Final output should be well-formatted markdown."""
        agent = NeumannAgent(config=AgentConfig(repo_path=str(tmp_repo)))
        result = agent.run("git status")
        # Should not be raw JSON or tool output
        assert "##" in result.output or "**" in result.output
        assert "{" not in result.output[:50]  # Not raw JSON start
