"""Neumann TUI — Terminal User Interface for the autonomous coding agent.

Built with Textual — modern reactive TUI framework.

Usage:
    pip install ".[tui]"
    neumann-tui

    # Or: python -m neumann.tui
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, Static, Input, Label, DataTable,
    ProgressBar, RichLog, Markdown, TabbedContent, TabPane,
)
from textual.reactive import reactive
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
from rich.table import Table as RichTable

from neumann import NeumannAgent, AgentConfig, AgentResult
from neumann import SelfImprovementEngine


# ═══════════════════════════════════════════════════════════════════
# Agent Status Widget
# ═══════════════════════════════════════════════════════════════════

class AgentStatusWidget(Static):
    """Displays current agent state with color-coded status."""

    status = reactive("idle")
    task = reactive("")
    progress = reactive(0.0)

    def render(self) -> Panel:
        status_icons = {
            "idle": "⏸️ ",
            "thinking": "🧠",
            "planning": "📋",
            "executing": "⚡",
            "observing": "👁️ ",
            "correcting": "🔧",
            "done": "✅",
            "error": "❌",
            "waiting": "💬",
        }
        status_colors = {
            "idle": "dim",
            "thinking": "yellow",
            "planning": "blue",
            "executing": "cyan",
            "observing": "magenta",
            "correcting": "orange3",
            "done": "green",
            "error": "red",
            "waiting": "white",
        }

        icon = status_icons.get(self.status, "❓")
        color = status_colors.get(self.status, "white")

        status_text = Text()
        status_text.append(f"{icon} ", style=color)
        status_text.append(f"Agent: ", style="bold")
        status_text.append(self.status.upper(), style=f"bold {color}")

        if self.task:
            status_text.append(" — ", style="dim")
            status_text.append(self.task[:60], style="italic")

        if self.progress > 0:
            pct = int(self.progress * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            status_text.append(f" [{bar}] {pct}%")

        return Panel(status_text, border_style=color)


# ═══════════════════════════════════════════════════════════════════
# Plan Widget
# ═══════════════════════════════════════════════════════════════════

class PlanWidget(Static):
    """Displays the current plan with sub-task status."""

    plan_data = reactive([])

    def render(self) -> Panel:
        if not self.plan_data:
            return Panel(
                Text("No active plan", style="dim italic"),
                title="📋 Plan",
                border_style="dim",
            )

        lines: list[Text] = []
        for task in self.plan_data:
            status = task.get("status", "pending")
            icon = {"done": "✅", "failed": "❌", "skipped": "⏭️",
                    "running": "🔄", "pending": "⏳"}.get(status, "⏳")
            tool = task.get("tool", "?")
            desc = task.get("description", "")[:50]

            line = Text()
            line.append(f"{icon} ", style="default")
            line.append(f"[{tool}] ", style="bold cyan")
            line.append(desc, style="dim" if status == "pending" else "")
            if task.get("error"):
                line.append(f" — {task['error'][:30]}", style="red")
            lines.append(line)

        table = RichTable.grid(padding=(0, 1))
        table.add_column()
        for line in lines:
            table.add_row(line)

        return Panel(table, title=f"📋 Plan ({len(self.plan_data)} steps)", border_style="blue")


# ═══════════════════════════════════════════════════════════════════
# Tool Call Widget
# ═══════════════════════════════════════════════════════════════════

class ToolCallWidget(Static):
    """Displays recent tool calls with status."""

    tool_calls = reactive([])

    def render(self) -> Panel:
        if not self.tool_calls:
            return Panel(
                Text("No tool calls yet", style="dim italic"),
                title="🔧 Tool Calls",
                border_style="dim",
            )

        table = RichTable.grid(padding=(0, 1))
        table.add_column()
        table.add_column()
        table.add_column()

        for tc in self.tool_calls[-15:]:
            icon = "✅" if tc.get("success") else "❌"
            tool = tc.get("tool", "?")
            input_str = json.dumps(tc.get("input", {}))[:30]
            table.add_row(
                Text(icon, style="default"),
                Text(tool, style="bold cyan"),
                Text(input_str, style="dim"),
            )

        return Panel(table, title=f"🔧 Tool Calls ({len(self.tool_calls)})", border_style="cyan")


# ═══════════════════════════════════════════════════════════════════
# Stats Widget
# ═══════════════════════════════════════════════════════════════════

class StatsWidget(Static):
    """Displays self-improvement and performance stats."""

    stats_data = reactive({})
    task_count = reactive(0)
    success_rate = reactive(0.0)

    def render(self) -> Panel:
        table = RichTable.grid(padding=(0, 1))
        table.add_column(style="bold")
        table.add_column()

        # Performance
        table.add_row(Text("📊 Tasks:", style="default"), Text(str(self.task_count), style="bold green"))
        sr = f"{self.success_rate:.0%}" if self.task_count > 0 else "N/A"
        table.add_row(Text("✅ Success:", style="default"), Text(sr, style="bold green"))

        # Self-improvement
        si = self.stats_data
        if si:
            exp = si.get("total_experiences", 0)
            table.add_row(Text("🧠 Experience:", style="default"), Text(str(exp), style="bold yellow"))
            patterns = si.get("patterns_found", 0)
            table.add_row(Text("🔍 Patterns:", style="default"), Text(str(patterns), style="bold magenta"))
            strategies = si.get("strategies_learned", 0)
            table.add_row(Text("🎯 Strategies:", style="default"), Text(str(strategies), style="bold blue"))
            tools = si.get("generated_tools", 0)
            table.add_row(Text("🔧 Tools:", style="default"), Text(str(tools), style="bold cyan"))

        return Panel(table, title="📊 Stats", border_style="yellow")


# ═══════════════════════════════════════════════════════════════════
# Output Widget
# ═══════════════════════════════════════════════════════════════════

class OutputWidget(Static):
    """Displays agent output with markdown rendering."""

    output_text = reactive("")

    def render(self) -> Panel:
        if not self.output_text:
            return Panel(
                Text("Agent output will appear here...", style="dim italic"),
                title="📝 Output",
                border_style="dim",
            )
        return Panel(
            Markdown(self.output_text),
            title="📝 Output",
            border_style="green",
            height=self.size.height - 2 if self.size else None,
        )


# ═══════════════════════════════════════════════════════════════════
# Main TUI App
# ═══════════════════════════════════════════════════════════════════

class NeumannTUI(App):
    """Neumann Agent Terminal User Interface."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #top-bar {
        height: 3;
        dock: top;
    }
    #main-area {
        layout: horizontal;
        height: 1fr;
    }
    #left-panel {
        width: 35%;
        layout: vertical;
    }
    #right-panel {
        width: 65%;
        layout: vertical;
    }
    #plan-widget, #tool-widget, #stats-widget {
        height: auto;
        max-height: 40%;
    }
    #output-widget {
        height: 1fr;
    }
    #input-area {
        height: 3;
        dock: bottom;
        layout: horizontal;
    }
    #task-input {
        width: 1fr;
        height: 1;
    }
    #send-btn {
        width: 10;
        height: 1;
    }
    """

    TITLE = "Neumann Agent"
    SUB_TITLE = "Autonomous Coding Agent"

    def __init__(self, repo_path: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.repo_path = repo_path
        self.agent: NeumannAgent | None = None
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Container(id="top-bar"):
            yield AgentStatusWidget(id="agent-status")

        with Horizontal(id="main-area"):
            with Vertical(id="left-panel"):
                yield PlanWidget(id="plan-widget")
                yield ToolCallWidget(id="tool-widget")
                yield StatsWidget(id="stats-widget")

            with Vertical(id="right-panel"):
                yield OutputWidget(id="output-widget")

        with Container(id="input-area"):
            yield Input(placeholder="Enter a task (e.g., 'fix the bug in main.py')...", id="task-input")
            yield Static("▶", id="send-btn")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize agent on mount."""
        cfg = AgentConfig(repo_path=self.repo_path)
        self.agent = NeumannAgent(config=cfg)
        self.post_message("ready")
        self.notify("Neumann Agent ready", title="🤖")
        self.query_one("#task-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle task submission."""
        task = event.value.strip()
        if not task or self._running:
            return

        self._running = True
        self.query_one("#task-input", Input).value = ""
        self.query_one("#task-input", Input).disabled = True

        # Update status
        status = self.query_one("#agent-status", AgentStatusWidget)
        status.status = "thinking"
        status.task = task

        # Clear previous output
        output = self.query_one("#output-widget", OutputWidget)
        output.output_text = f"## Task: {task}\n\n🔄 Starting..."

        # Run agent asynchronously
        result = await self.run_agent_task(task)

        # Update output
        output.output_text = result.output

        # Update stats
        self.update_stats(result)

        # Reset status
        status.status = "done" if result.status == "done" else "error"
        status.task = ""
        status.progress = 0.0

        self._running = False
        self.query_one("#task-input", Input).disabled = False
        self.query_one("#task-input", Input).focus()

    async def run_agent_task(self, task: str) -> AgentResult:
        """Run agent in background, updating UI in real-time."""
        result = None

        def run():
            nonlocal result
            result = self.agent.run(task)

        # Run in thread
        loop = asyncio.get_event_loop()
        agent_task = loop.run_in_executor(None, run)

        # Poll for updates
        while not agent_task.done():
            await asyncio.sleep(0.3)

            # Update status from agent
            if self.agent:
                status = self.query_one("#agent-status", AgentStatusWidget)
                status.status = self.agent.status.value

                # Update plan
                plan = self.query_one("#plan-widget", PlanWidget)
                if self.agent.current_plan:
                    plan.plan_data = [st.to_dict() for st in self.agent.current_plan.subtasks]
                    status.progress = self.agent.current_plan.progress

                # Update tool calls
                tools = self.query_one("#tool-widget", ToolCallWidget)
                tools.tool_calls = self.agent._tool_calls_log

        await agent_task
        return result

    def update_stats(self, result: AgentResult) -> None:
        """Update stats widget."""
        stats = self.query_one("#stats-widget", StatsWidget)
        stats.task_count += 1
        if result.status == "done":
            stats.success_rate = (
                (stats.success_rate * (stats.task_count - 1) + 1) / stats.task_count
            )
        else:
            stats.success_rate = (
                (stats.success_rate * (stats.task_count - 1)) / stats.task_count
            )

        if self.agent:
            stats.stats_data = self.agent.self_improve.stats()


def run_tui(repo_path: str | None = None) -> None:
    """Launch the Neumann TUI."""
    app = NeumannTUI(repo_path=repo_path)
    app.run()


if __name__ == "__main__":
    run_tui()
