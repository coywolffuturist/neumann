"""Autonomous Agent Loop — think → plan → act → observe → repeat.

This is the core of Neumann as an autonomous coding agent.
Unlike a simple tool wrapper, the agent:
1. Thinks about what needs to be done
2. Creates a concrete plan with sub-tasks
3. Executes each sub-task using available tools
4. Observes results and adjusts plan
5. Self-corrects on errors
6. Reports back when the task is complete

Architecture:
- AgentBrain: LLM-powered reasoning + planning
- TaskPlan: Structured plan with sub-tasks
- AgentLoop: Main execution loop with safety controls
- AgentState: Tracks current state across iterations

Usage:
    from neumann.agent import NeumannAgent, AgentConfig

    agent = NeumannAgent(
        repo_path="/home/user/project",
        model="gpt-4o",  # or "claude-sonnet-4-20250514", "qwen2.5-coder"
    )
    result = agent.run("Add a logging system to the project")

    # Interactive REPL mode
    agent.repl()
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any
from pathlib import Path

from .advanced_prompts import AdvancedPromptEngine, PromptContext
from .memory import AgentMemory
from .tools.registry import register_defaults, execute_tool, list_tools
from .git_tools import GitTools
from .logger import NeumannLogger
from .self_improvement import SelfImprovementEngine
from .llm.router import LLMRouter, LLMConfig
from .llm import LLMMessage, LLMResponse


# ═══════════════════════════════════════════════════════════════════
# Agent State & Types
# ═══════════════════════════════════════════════════════════════════

class AgentStatus(str, Enum):
    THINKING = "thinking"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    CORRECTING = "correcting"
    DONE = "done"
    ERROR = "error"
    WAITING = "waiting"  # waiting for user input


@dataclass
class SubTask:
    """A single step in the agent's plan."""
    id: int
    description: str
    tool: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | running | done | failed | skipped
    result: str = ""
    error: str = ""
    retries: int = 0
    max_retries: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskPlan:
    """The agent's plan for completing a task."""
    summary: str = ""
    reasoning: str = ""
    subtasks: list[SubTask] = field(default_factory=list)
    completed: int = 0
    failed: int = 0
    total: int = 0

    @property
    def is_complete(self) -> bool:
        return self.completed + self.failed >= self.total

    @property
    def progress(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.completed + self.failed) / self.total

    def next_pending(self) -> SubTask | None:
        for st in self.subtasks:
            if st.status == "pending":
                return st
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "reasoning": self.reasoning,
            "subtasks": [st.to_dict() for st in self.subtasks],
            "completed": self.completed,
            "failed": self.failed,
            "total": self.total,
            "progress": self.progress,
        }


@dataclass
class AgentResult:
    """Final result from an agent run."""
    task: str
    status: str
    plan: TaskPlan = field(default_factory=TaskPlan)
    output: str = ""
    errors: list[str] = field(default_factory=list)
    iterations: int = 0
    duration_ms: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
# System prompts for agent loop
# ═══════════════════════════════════════════════════════════════════

THINKING_PROMPT = """\
You are Neumann Agent — an autonomous coding assistant running locally.
The user has given you a task. Think through what needs to be done.

Consider:
1. What is the user's actual goal? (not just their literal request)
2. What information do you need? (files, git status, project structure)
3. What tools should you use and in what order?
4. Are there any risks or destructive operations to warn about?

Available tools: {tools}

Project context: {context}
"""

CORRECTION_PROMPT = """\
Your previous sub-task failed. Analyze the error and decide:
1. Can you fix it with a different approach?
2. Do you need more information first?
3. Should this sub-task be skipped?

Format as JSON:
{
    "action": "retry" | "skip" | "need_info",
    "reasoning": "Why you chose this action",
    "new_tool": "..." ,
    "new_tool_input": {...}
}

Error: {error}
Previous attempt: {previous}
"""


# ═══════════════════════════════════════════════════════════════════
# Agent Configuration
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AgentConfig:
    """Configuration for the autonomous agent."""
    # LLM settings
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 4096
    llm_config: LLMConfig | None = None  # Optional: custom LLM config

    # LLM provider: "ollama" | "openai" | "anthropic" | "auto"
    # "auto" = try all available, prefer Ollama (free, local)
    llm_provider: str = "auto"

    # Loop control
    max_iterations: int = 50  # Safety limit on loop iterations
    max_subtask_retries: int = 2  # Retries per sub-task before skipping

    # Safety
    require_confirmation: bool = False  # Ask before destructive ops
    allowed_tools: set[str] = field(default_factory=lambda: {
        "bash", "read_file", "write_file", "edit_file", "grep", "git",
    })

    # Paths
    repo_path: str | None = None

    # Logging
    log_level: str = "INFO"

    # System prompt override
    system_prompt: str = ""


# ═══════════════════════════════════════════════════════════════════
# Autonomous Agent
# ═══════════════════════════════════════════════════════════════════

class NeumannAgent:
    """Autonomous coding agent with think → plan → act → observe loop."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        **kwargs: Any,
    ) -> None:
        self.config = config or AgentConfig(**kwargs)

        # Core components
        self.logger = NeumannLogger(level=self.config.log_level)
        self.memory = AgentMemory(
            system_prompt=self.config.system_prompt or "You are Neumann Agent — an autonomous coding assistant."
        )
        self.prompt_engine = AdvancedPromptEngine()
        self._tool_calls_log: list[dict[str, Any]] = []

        # Self-improvement engine
        repo_path = Path(self.config.repo_path or ".")
        storage_dir = str(repo_path)
        self.self_improve = SelfImprovementEngine(storage_dir=storage_dir)

        # LLM router
        self.llm: LLMRouter | None = None
        try:
            llm_cfg = self.config.llm_config or LLMConfig(
                default_openai_model=self.config.model,
                default_ollama_model="qwen2.5-coder",
                timeout=self.config.max_tokens // 10 + 60,
            )
            self.llm = LLMRouter(config=llm_cfg)
            self.logger._log.debug("LLM router initialized with adapters: %s", self.llm.list_adapters())
        except Exception as e:
            self.logger._log.warning("LLM router not available: %s — using rule-based planning", e)

        # Register tools
        register_defaults(repo_path=self.config.repo_path)

        # Git tools (if repo path is set)
        self.git: GitTools | None = None
        if self.config.repo_path:
            try:
                repo_p = Path(self.config.repo_path)
                if repo_p.exists():
                    self.git = GitTools(repo_path=self.config.repo_path)
            except (ValueError, FileNotFoundError, OSError):
                pass

        # State
        self._status = AgentStatus.WAITING
        self._current_plan: TaskPlan | None = None
        self._iteration_count = 0

    # ── main entry point ──────────────────────────────────────────

    def run(self, task: str) -> AgentResult:
        """Run the autonomous agent loop on a task.
        
        The agent will:
        1. Think about what needs to be done
        2. Create a plan with sub-tasks
        3. Execute each sub-task
        4. Observe results and self-correct
        5. Return final result
        """
        start = time.perf_counter()
        self.logger._log.info("Agent starting: %s", task)

        self._status = AgentStatus.THINKING
        self.memory.add_user(task)

        # Phase 1: Think & Plan
        plan = self._think_and_plan(task)
        self._current_plan = plan
        self._status = AgentStatus.PLANNING

        if not plan.subtasks:
            # No tool needed — answer directly
            self._status = AgentStatus.DONE
            result = self._direct_answer(task)
            result.duration_ms = (time.perf_counter() - start) * 1000
            return result

        self.logger._log.info("Plan created: %d subtasks", plan.total)
        self.memory.add_assistant(f"Plan: {plan.summary}")

        # Phase 2: Execute
        self._status = AgentStatus.EXECUTING
        errors: list[str] = []

        for iteration in range(self.config.max_iterations):
            self._iteration_count = iteration + 1

            subtask = plan.next_pending()
            if subtask is None:
                break  # All done

            # Execute sub-task
            self._status = AgentStatus.EXECUTING
            result_data = self._execute_subtask(subtask)

            # Observe
            self._status = AgentStatus.OBSERVING
            if result_data["success"]:
                subtask.status = "done"
                subtask.result = result_data["output"]
                plan.completed += 1
                self.memory.add_tool_result(subtask.tool, result_data["output"])
                self.logger._log.info(
                    "Subtask %d/%d done: %s",
                    plan.completed + plan.failed, plan.total,
                    subtask.description[:60],
                )
            else:
                # Self-correction
                subtask.retries += 1
                if subtask.retries < subtask.max_retries:
                    self._status = AgentStatus.CORRECTING
                    correction = self._self_correct(subtask, result_data["error"])
                    if correction["action"] == "retry":
                        subtask.tool = correction.get("new_tool", subtask.tool)
                        subtask.tool_input = correction.get("new_tool_input", subtask.tool_input)
                        subtask.status = "pending"  # Will retry next iteration
                        continue
                    elif correction["action"] == "skip":
                        subtask.status = "skipped"
                        plan.failed += 1
                        errors.append(f"Skipped: {subtask.description}")
                        continue
                else:
                    # Max retries exceeded
                    subtask.status = "failed"
                    subtask.error = result_data["error"]
                    plan.failed += 1
                    errors.append(f"Failed: {subtask.description}: {result_data['error']}")
                    self.logger.error(
                        error_type="SubTaskFailure",
                        component="AgentLoop",
                        message=subtask.description,
                    )

        # Phase 3: Done
        self._status = AgentStatus.DONE

        # Build final output
        output = self._build_final_output(task, plan)
        self.memory.add_assistant(output)

        duration_ms = (time.perf_counter() - start) * 1000

        # Log experience for self-improvement
        tools_used = [st.tool for st in plan.subtasks if st.tool]
        self.self_improve.log_experience(
            task=task,
            task_type=self._detect_task_type_from_plan(plan),
            tools_used=tools_used,
            tool_sequence=tools_used,
            success=plan.failed == 0,
            errors=errors,
            duration_ms=round(duration_ms, 3),
            iterations=self._iteration_count,
            subtasks_total=plan.total,
            subtasks_completed=plan.completed,
            subtasks_failed=plan.failed,
        )

        self.logger._log.info(
            "Agent done: %d/%d subtasks, %.1fs",
            plan.completed, plan.total, duration_ms / 1000,
        )

        return AgentResult(
            task=task,
            status="done" if plan.failed == 0 else "partial",
            plan=plan,
            output=output,
            errors=errors,
            iterations=self._iteration_count,
            duration_ms=round(duration_ms, 3),
            tool_calls=self._tool_calls_log,
        )

    # ── thinking & planning ───────────────────────────────────────

    def _think_and_plan(self, task: str) -> TaskPlan:
        """Use LLM to think about the task and create a plan.
        
        Tries LLM first. Falls back to rule-based planning if LLM unavailable.
        """
        # Try LLM-based planning first
        if self.llm:
            try:
                plan = self._llm_based_plan(task)
                if plan and plan.subtasks:
                    self.logger._log.info("LLM-based plan created: %d steps", plan.total)
                    return plan
            except Exception as e:
                self.logger._log.warning("LLM planning failed, using rule-based: %s", e)

        # Fallback to rule-based planning
        plan = self._rule_based_plan(task)
        self.logger._log.info("Rule-based plan created: %d steps", plan.total)
        return plan

    def _llm_based_plan(self, task: str) -> TaskPlan | None:
        """Create a plan using the LLM."""
        context = self._build_context()
        tools_info = json.dumps(list_tools(), indent=2)

        system_prompt = f"""\
You are Neumann Agent — an autonomous coding assistant running locally.
You have access to tools and must plan carefully before acting.

Available tools:
{tools_info}

Project context:
{context}

CRITICAL RULES:
1. ALWAYS read a file before editing — never write blind
2. Use the SMALLEST tool for the job
3. Chain tools: read → edit → verify → test → commit
4. Return ONLY valid JSON — no explanation, no markdown
5. If you don't need tools, return empty subtasks array

Format your response as EXACTLY this JSON (no other text):
{{"summary": "brief task description", "reasoning": "your thinking", "subtasks": [{{"id": 1, "description": "step description", "tool": "tool_name", "tool_input": {{"key": "value"}}}}]}}
"""

        try:
            response = self.llm.chat(
                prompt=f"Task: {task}\n\nPlan the steps needed to complete this task.",
                system_prompt=system_prompt,
                model=self.config.model,
                temperature=0.3,  # Lower temperature for more deterministic planning
                max_tokens=self.config.max_tokens,
            )

            # Parse the JSON response
            plan_data = self._parse_llm_plan(response.content, task)
            if plan_data:
                return plan_data
        except Exception as e:
            self.logger._log.warning("LLM plan generation failed: %s", e)

        return None

    def _parse_llm_plan(self, content: str, task: str) -> TaskPlan | None:
        """Parse LLM response into a TaskPlan."""
        # Extract JSON from response (may have markdown formatting)
        json_str = content.strip()

        # Remove markdown code blocks if present
        if json_str.startswith("```"):
            json_str = json_str.split("```", 2)[-2].strip()
            if json_str.startswith("json"):
                json_str = json_str[4:].strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(content[start:end])
                except json.JSONDecodeError:
                    return None
            else:
                return None

        # Validate structure
        if "subtasks" not in data:
            return None

        plan = TaskPlan(
            summary=data.get("summary", task),
            reasoning=data.get("reasoning", ""),
            subtasks=[],
            total=0,
        )

        for i, st_data in enumerate(data["subtasks"], 1):
            if isinstance(st_data, dict) and st_data.get("tool"):
                plan.subtasks.append(SubTask(
                    id=i,
                    description=st_data.get("description", ""),
                    tool=st_data.get("tool", ""),
                    tool_input=st_data.get("tool_input", {}),
                ))

        plan.total = len(plan.subtasks)
        return plan if plan.subtasks else None

    def _rule_based_plan(self, task: str) -> TaskPlan:
        """Create a plan using heuristic rules (LLM-independent)."""
        task_lower = task.lower()
        plan = TaskPlan()
        subtasks: list[SubTask] = []
        task_id = 0

        # Pattern: edit/fix a specific file
        import re
        file_match = re.search(r'(?:in|file|at)\s+([`\w./-]+\.\w+)', task_lower)
        file_path = file_match.group(1).strip('`') if file_match else None

        if file_path and ("fix" in task_lower or "edit" in task_lower or "change" in task_lower):
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description=f"Read {file_path} to understand current code",
                tool="read_file",
                tool_input={"file_path": file_path},
            ))
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description=f"Fix the issue in {file_path}",
                tool="edit_file",
                tool_input={"file_path": file_path},
            ))
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description=f"Verify the fix in {file_path}",
                tool="read_file",
                tool_input={"file_path": file_path},
            ))

        elif "git status" in task_lower or "status" == task_lower:
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description="Check git status",
                tool="git",
                tool_input={"action": "status"},
            ))

        elif "commit" in task_lower:
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description="Check git status before commit",
                tool="git",
                tool_input={"action": "status"},
            ))
            task_id += 1
            msg_match = re.search(r'["\'](.+?)["\']', task)
            message = msg_match.group(1) if msg_match else task
            subtasks.append(SubTask(
                id=task_id,
                description=f"Commit with message: {message}",
                tool="git",
                tool_input={"action": "commit", "message": message},
            ))

        elif "create" in task_lower or "write" in task_lower or "new file" in task_lower:
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description=f"Create file: {task}",
                tool="write_file",
                tool_input={},
            ))

        elif "search" in task_lower or "find" in task_lower or "grep" in task_lower:
            pattern = task_lower.replace("search", "").replace("find", "").replace("grep", "").replace("for", "").strip()
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description=f"Search for: {pattern}",
                tool="grep",
                tool_input={"pattern": pattern},
            ))

        elif any(kw in task_lower for kw in ["list", "show", "branches"]):
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description="List branches",
                tool="git",
                tool_input={"action": "branches"},
            ))

        elif any(kw in task_lower for kw in ["log", "history", "commits"]):
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description="Show git log",
                tool="git",
                tool_input={"action": "log", "n": 10},
            ))

        elif any(kw in task_lower for kw in ["run", "execute", "command", "test"]):
            task_id += 1
            subtasks.append(SubTask(
                id=task_id,
                description=f"Run: {task}",
                tool="bash",
                tool_input={"command": task},
            ))

        # Set plan summary
        if subtasks:
            plan.summary = f"Execute {len(subtasks)} steps to complete: {task}"
            plan.reasoning = "Rule-based plan generated heuristically"
        else:
            plan.summary = f"General task: {task}"
            plan.reasoning = "No specific tool pattern matched — general assistance mode"

        plan.subtasks = subtasks
        plan.total = len(subtasks)
        return plan

    # ── execution ─────────────────────────────────────────────────

    def _execute_subtask(self, subtask: SubTask) -> dict[str, Any]:
        """Execute a single sub-task using the specified tool."""
        if not subtask.tool:
            return {"success": False, "error": "No tool specified", "output": ""}

        if subtask.tool not in self.config.allowed_tools:
            return {
                "success": False,
                "error": f"Tool '{subtask.tool}' not in allowed tools",
                "output": "",
            }

        subtask.status = "running"
        self.logger._log.info("Executing: %s (%s)", subtask.tool, subtask.description[:60])

        try:
            result = execute_tool(subtask.tool, **subtask.tool_input)

            self._tool_calls_log.append({
                "tool": subtask.tool,
                "input": subtask.tool_input,
                "success": result.success,
                "timestamp": time.time(),
            })

            return {
                "success": result.success,
                "output": result.output or result.to_dict().get("output", ""),
                "error": result.error,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "output": "",
            }

    # ── self-correction ───────────────────────────────────────────

    def _self_correct(
        self, subtask: SubTask, error: str
    ) -> dict[str, Any]:
        """Analyze error and decide correction strategy."""
        error_lower = error.lower()

        # File not found — try to find it
        if "not found" in error_lower and subtask.tool == "read_file":
            return {
                "action": "skip",
                "reasoning": f"File does not exist: {subtask.tool_input.get('file_path', 'unknown')}",
            }

        # String not found in edit — try grep first
        if "not found" in error_lower and subtask.tool == "edit_file":
            return {
                "action": "skip",
                "reasoning": "String not found in file — needs manual review",
            }

        # Command not allowed
        if "not in allowed" in error_lower:
            return {
                "action": "skip",
                "reasoning": "Command not in allowed list",
            }

        # Default: retry once
        if subtask.retries < subtask.max_retries:
            return {
                "action": "retry",
                "reasoning": f"Retrying (attempt {subtask.retries + 1})",
            }

        return {
            "action": "skip",
            "reasoning": f"Max retries exceeded: {error[:100]}",
        }

    # ── output building ───────────────────────────────────────────

    def _detect_task_type_from_plan(self, plan: TaskPlan) -> str:
        """Detect task type from plan tools."""
        if not plan.subtasks:
            return "general"
        tools = [st.tool for st in plan.subtasks if st.tool]
        if "read_file" in tools and "edit_file" in tools:
            return "edit_file"
        if "write_file" in tools:
            return "write_file"
        if "git" in tools:
            return "git"
        if "grep" in tools:
            return "search"
        if "bash" in tools:
            return "bash"
        return "general"

    def _build_final_output(self, task: str, plan: TaskPlan) -> str:
        """Build a human-readable final output."""
        lines = [f"## Task: {task}", ""]

        if plan.summary:
            lines.append(f"**{plan.summary}**")
            lines.append("")

        lines.append(f"### Results: {plan.completed}/{plan.total} completed")
        lines.append("")

        for st in plan.subtasks:
            status_icon = {"done": "✅", "failed": "❌", "skipped": "⏭️", "running": "🔄"}.get(st.status, "❓")
            lines.append(f"{status_icon} [{st.tool}] {st.description}")
            if st.error:
                lines.append(f"   Error: {st.error[:100]}")
            if st.result and st.tool == "read_file":
                # Show first 3 lines of read results
                content = st.result[:300]
                if len(content) > 300:
                    content += "..."
                lines.append(f"   ```")
                lines.append(f"   {content}")
                lines.append(f"   ```")
            elif st.result and len(st.result) < 200:
                lines.append(f"   {st.result}")
            lines.append("")

        if plan.failed > 0:
            lines.append(f"\n⚠️ {plan.failed} sub-task(s) could not be completed.")

        return "\n".join(lines)

    def _direct_answer(self, task: str) -> AgentResult:
        """Return a direct answer without tool execution."""
        return AgentResult(
            task=task,
            status="done",
            output=f"I understand your request: '{task}'. However, I need more context about your project to provide a specific answer. Try giving me a task involving files, git operations, or code changes.",
            iterations=0,
        )

    # ── context building ──────────────────────────────────────────

    def _build_context(self) -> str:
        """Build project context for planning."""
        parts = []

        if self.git:
            try:
                status = self.git.status()
                parts.append(f"Git Branch: {status.branch}")
                parts.append(f"Git Clean: {status.clean}")
                if status.modified:
                    parts.append(f"Modified: {', '.join(status.modified[:5])}")
                if status.untracked:
                    parts.append(f"Untracked: {', '.join(status.untracked[:5])}")
            except Exception:
                pass

        parts.append(f"Working directory: {self.config.repo_path or Path.cwd()}")
        parts.append(f"Available tools: {', '.join(self.config.allowed_tools)}")

        return "\n".join(parts)

    # ── introspection ─────────────────────────────────────────────

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def current_plan(self) -> TaskPlan | None:
        return self._current_plan

    def get_stats(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "iteration": self._iteration_count,
            "plan": self._current_plan.to_dict() if self._current_plan else None,
            "tool_calls": len(self._tool_calls_log),
            "memory": self.memory.stats(),
            "logger": self.logger.metrics(),
        }

    def reset(self) -> None:
        """Reset agent state for a new task (keeps self-improvement data)."""
        self._status = AgentStatus.WAITING
        self._current_plan = None
        self._iteration_count = 0
        self._tool_calls_log.clear()
        self.memory.clear()
        # Note: self-improvement data is NOT reset — it persists across sessions
