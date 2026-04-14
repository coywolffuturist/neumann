"""Recursive Self-Improvement Engine for Neumann Agent.

Architecture:
1. ExperienceLog — records every task outcome, timing, tool usage
2. PatternExtractor — auto-detects success/failure patterns from history
3. StrategyOptimizer — selects best approach based on historical data
4. PromptAutoTuner — adjusts system prompts based on effectiveness
5. ToolGenerator — agent can create new Python tools when needed
6. KnowledgeBase — persistent lessons learned across sessions

The agent improves over time NOT by changing model weights,
but by building a rich database of experience that guides future decisions.

Usage:
    engine = SelfImprovementEngine()
    engine.log_experience(task="fix bug", tools_used=["read_file", "edit_file"],
                          success=True, duration_ms=5000, errors=[])
    insights = engine.get_insights("debug")
    best_strategy = engine.recommend_strategy("fix bug in file")
    engine.save("knowledge.json")
"""
from __future__ import annotations

import json
import time
import hashlib
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from collections import Counter


# ═══════════════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ExperienceEntry:
    """A single task experience record."""
    id: str
    task: str
    task_type: str  # debug, write, edit, review, refactor, git, search, general
    tools_used: list[str]
    tool_sequence: list[str]  # ordered list of tool calls
    success: bool
    errors: list[str]
    duration_ms: float
    iterations: int
    subtasks_total: int
    subtasks_completed: int
    subtasks_failed: int
    timestamp: float = field(default_factory=time.time)
    user_feedback: str = ""  # optional: user rating or comment
    lesson_learned: str = ""  # extracted or manually provided

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def generate_id(task: str) -> str:
        return hashlib.sha256(
            f"{task}-{time.time()}".encode()
        ).hexdigest()[:12]


@dataclass
class Pattern:
    """An extracted pattern from experience data."""
    description: str
    pattern_type: str  # "success" | "failure" | "efficiency" | "tool_choice"
    confidence: float  # 0.0 - 1.0
    evidence_count: int
    applies_to: list[str]  # task types this pattern applies to
    recommendation: str  # what to do based on this pattern

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Strategy:
    """A recommended approach for a task type."""
    task_type: str
    name: str
    tool_sequence: list[str]
    success_rate: float
    avg_duration_ms: float
    sample_count: int
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════
# Experience Log
# ═══════════════════════════════════════════════════════════════════

class ExperienceLog:
    """Records every agent task experience."""

    def __init__(self, max_entries: int = 10_000) -> None:
        self._entries: list[ExperienceEntry] = []
        self._max_entries = max_entries

    def log(self, entry: ExperienceEntry) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

    def get_entries(
        self,
        task_type: str | None = None,
        success: bool | None = None,
        limit: int = 100,
    ) -> list[ExperienceEntry]:
        entries = self._entries
        if task_type:
            entries = [e for e in entries if e.task_type == task_type]
        if success is not None:
            entries = [e for e in entries if e.success == success]
        return entries[-limit:]

    def get_all(self) -> list[ExperienceEntry]:
        return list(self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def success_rate(self) -> float:
        if not self._entries:
            return 0.0
        return sum(1 for e in self._entries if e.success) / len(self._entries)

    def save(self, path: str | Path) -> int:
        data = [e.to_dict() for e in self._entries]
        Path(path).write_text(json.dumps(data, indent=2))
        return len(data)

    def load(self, path: str | Path) -> int:
        if not Path(path).exists():
            return 0
        data = json.loads(Path(path).read_text())
        self._entries = [ExperienceEntry(**e) for e in data]
        return len(self._entries)


# ═══════════════════════════════════════════════════════════════════
# Pattern Extractor
# ═══════════════════════════════════════════════════════════════════

class PatternExtractor:
    """Analyzes experience log to find actionable patterns."""

    def __init__(self, log: ExperienceLog) -> None:
        self._log = log

    def extract_all(self) -> list[Pattern]:
        """Extract all patterns from experience data."""
        patterns: list[Pattern] = []
        patterns.extend(self._extract_tool_patterns())
        patterns.extend(self._extract_error_patterns())
        patterns.extend(self._extract_efficiency_patterns())
        return patterns

    def _extract_tool_patterns(self) -> list[Pattern]:
        """Find which tool combinations work best for each task type."""
        patterns: list[Pattern] = []
        entries = self._log.get_all()
        if len(entries) < 3:
            return patterns

        # Group by task type
        by_type: dict[str, list[ExperienceEntry]] = {}
        for e in entries:
            by_type.setdefault(e.task_type, []).append(e)

        for task_type, type_entries in by_type.items():
            if len(type_entries) < 2:
                continue

            # Find most common tool sequence among successes
            successful = [e for e in type_entries if e.success]
            if not successful:
                continue

            sequences = [tuple(e.tool_sequence) for e in successful if e.tool_sequence]
            if not sequences:
                continue

            seq_counter = Counter(sequences)
            most_common_seq, count = seq_counter.most_common(1)[0]
            confidence = count / len(successful)

            if confidence >= 0.5 and len(most_common_seq) > 0:
                patterns.append(Pattern(
                    description=f"Best tool sequence for {task_type}: {' → '.join(most_common_seq)}",
                    pattern_type="success",
                    confidence=confidence,
                    evidence_count=count,
                    applies_to=[task_type],
                    recommendation=f"Use tools in order: {', '.join(most_common_seq)}",
                ))

        return patterns

    def _extract_error_patterns(self) -> list[Pattern]:
        """Find common failure patterns."""
        patterns: list[Pattern] = []
        entries = self._log.get_all()
        failures = [e for e in entries if not e.success]

        if len(failures) < 2:
            return patterns

        # Group errors by similarity
        error_groups: dict[str, list[ExperienceEntry]] = {}
        for e in failures:
            if not e.errors:
                continue
            # Normalize error to find patterns
            for err in e.errors:
                key = self._normalize_error(err)
                error_groups.setdefault(key, []).append(e)

        for error_key, group in error_groups.items():
            if len(group) >= 2:
                task_types = list(set(e.task_type for e in group))
                patterns.append(Pattern(
                    description=f"Common failure: {error_key}",
                    pattern_type="failure",
                    confidence=min(len(group) / 5, 1.0),
                    evidence_count=len(group),
                    applies_to=task_types,
                    recommendation=f"Avoid approaches that cause: {error_key}",
                ))

        return patterns

    def _extract_efficiency_patterns(self) -> list[Pattern]:
        """Find fastest approaches for each task type."""
        patterns: list[Pattern] = []
        entries = self._log.get_all()
        if len(entries) < 3:
            return patterns

        by_type: dict[str, list[ExperienceEntry]] = {}
        for e in entries:
            if e.success and e.duration_ms > 0:
                by_type.setdefault(e.task_type, []).append(e)

        for task_type, type_entries in by_type.items():
            if len(type_entries) < 2:
                continue

            avg_duration = sum(e.duration_ms for e in type_entries) / len(type_entries)
            fast = [e for e in type_entries if e.duration_ms < avg_duration * 0.7]

            if fast:
                common_tools = [e.tool_sequence for e in fast if e.tool_sequence]
                if common_tools:
                    tool_counts = Counter(t for seq in common_tools for t in seq)
                    top_tools = [t for t, _ in tool_counts.most_common(3)]

                    patterns.append(Pattern(
                        description=f"Fast approach for {task_type} (avg {avg_duration:.0f}ms)",
                        pattern_type="efficiency",
                        confidence=len(fast) / len(type_entries),
                        evidence_count=len(fast),
                        applies_to=[task_type],
                        recommendation=f"For speed, use: {', '.join(top_tools)}",
                    ))

        return patterns

    @staticmethod
    def _normalize_error(error: str) -> str:
        """Normalize error message for pattern matching."""
        # Remove specific file paths and values
        normalized = re.sub(r'[/\\][\w./-]+', '<path>', error)
        normalized = re.sub(r'\d+', '<n>', normalized)
        normalized = re.sub(r'"[^"]*"', '<str>', normalized)
        return normalized[:100]


# ═══════════════════════════════════════════════════════════════════
# Strategy Optimizer
# ═══════════════════════════════════════════════════════════════════

class StrategyOptimizer:
    """Selects the best strategy based on historical performance data."""

    def __init__(self, log: ExperienceLog) -> None:
        self._log = log
        self._strategies: dict[str, list[Strategy]] = {}
        self._rebuild_cache()

    def recommend(self, task_type: str, prioritize: str = "success") -> Strategy | None:
        """Get the best strategy for a task type.
        
        prioritize: "success" | "speed" | "reliability"
        """
        strategies = self._strategies.get(task_type, [])
        if not strategies:
            return self._default_strategy(task_type)

        if prioritize == "speed":
            return min(strategies, key=lambda s: s.avg_duration_ms)
        elif prioritize == "reliability":
            return max(strategies, key=lambda s: s.sample_count)
        else:  # success (default)
            return max(strategies, key=lambda s: s.success_rate)

    def _default_strategy(self, task_type: str) -> Strategy:
        """Return a default strategy when no data exists."""
        defaults: dict[str, list[str]] = {
            "debug": ["read_file", "grep", "edit_file", "read_file"],
            "write_file": ["write_file", "read_file"],
            "edit_file": ["read_file", "edit_file", "read_file"],
            "review": ["read_file", "grep"],
            "refactor": ["read_file", "edit_file", "read_file"],
            "git": ["git"],
            "search": ["grep", "read_file"],
            "bash": ["bash"],
        }
        return Strategy(
            task_type=task_type,
            name="default",
            tool_sequence=defaults.get(task_type, ["read_file"]),
            success_rate=0.5,
            avg_duration_ms=0.0,
            sample_count=0,
        )

    def _rebuild_cache(self) -> None:
        """Rebuild strategy cache from experience log."""
        entries = self._log.get_all()
        by_type: dict[str, list[ExperienceEntry]] = {}
        for e in entries:
            by_type.setdefault(e.task_type, []).append(e)

        self._strategies = {}
        for task_type, type_entries in by_type.items():
            # Group by tool sequence
            by_sequence: dict[tuple, list[ExperienceEntry]] = {}
            for e in type_entries:
                seq = tuple(e.tool_sequence) if e.tool_sequence else ()
                by_sequence.setdefault(seq, []).append(e)

            strategies: list[Strategy] = []
            for seq, seq_entries in by_sequence.items():
                success_rate = sum(1 for e in seq_entries if e.success) / len(seq_entries)
                avg_duration = sum(e.duration_ms for e in seq_entries) / len(seq_entries)

                strategies.append(Strategy(
                    task_type=task_type,
                    name=f"{'_'.join(seq[:3])}",
                    tool_sequence=list(seq),
                    success_rate=success_rate,
                    avg_duration_ms=avg_duration,
                    sample_count=len(seq_entries),
                ))

            self._strategies[task_type] = strategies

    def get_all_strategies(self, task_type: str) -> list[Strategy]:
        """Get all known strategies for a task type."""
        return self._strategies.get(task_type, [])

    @property
    def total_strategies(self) -> int:
        return sum(len(s) for s in self._strategies.values())


# ═══════════════════════════════════════════════════════════════════
# Prompt Auto-Tuner
# ═══════════════════════════════════════════════════════════════════

class PromptAutoTuner:
    """Adjusts system prompts based on what works best."""

    def __init__(self, log: ExperienceLog) -> None:
        self._log = log
        self._adjustments: list[dict[str, Any]] = []

    def analyze_and_suggest(self) -> list[dict[str, str]]:
        """Analyze experience data and suggest prompt improvements."""
        entries = self._log.get_all()
        suggestions: list[dict[str, str]] = []

        if len(entries) < 3:
            return suggestions

        # Check: Are certain tool instructions being followed?
        tool_violations = self._count_tool_violations(entries)
        for tool_name, violations in tool_violations.items():
            if violations > 3:
                suggestions.append({
                    "type": "reinforce_tool_usage",
                    "suggestion": f"Strengthen instruction for '{tool_name}' usage. "
                                  f"Failed {violations} times due to misuse.",
                    "action": "Add more explicit examples of correct '{tool_name}' usage in system prompt.",
                })

        # Check: Are error patterns addressable via prompts?
        repeated_errors = self._find_repeated_errors(entries)
        for error, count in repeated_errors:
            if count >= 2:
                suggestions.append({
                    "type": "prevent_repeated_error",
                    "suggestion": f"Error '{error}' occurred {count} times.",
                    "action": f"Add explicit warning about '{error}' in system prompt.",
                })

        # Check: Task types with low success rate
        success_by_type = self._success_rate_by_type(entries)
        for task_type, rate in success_by_type.items():
            if rate < 0.5 and len([e for e in entries if e.task_type == task_type]) >= 3:
                suggestions.append({
                    "type": "low_success_rate",
                    "suggestion": f"Task type '{task_type}' has {rate:.0%} success rate.",
                    "action": f"Improve system prompt instructions for '{task_type}' tasks.",
                })

        self._adjustments.extend(suggestions)
        return suggestions

    def apply_suggestions(self, current_prompt: str, suggestions: list[dict[str, str]]) -> str:
        """Apply suggestions to modify the current prompt."""
        modified = current_prompt
        for suggestion in suggestions:
            if suggestion["type"] == "reinforce_tool_usage":
                modified += f"\n\n⚠️ IMPORTANT: {suggestion['suggestion']}"
            elif suggestion["type"] == "prevent_repeated_error":
                modified += f"\n\n⚠️ AVOID: {suggestion['suggestion']}"
            elif suggestion["type"] == "low_success_rate":
                modified += f"\n\n📊 NOTE: {suggestion['suggestion']}"
        return modified

    def _count_tool_violations(self, entries: list[ExperienceEntry]) -> dict[str, int]:
        """Count tasks where tools were used incorrectly."""
        violations: dict[str, int] = {}
        for e in entries:
            if not e.success and e.errors:
                for err in e.errors:
                    for tool in e.tools_used:
                        if tool.lower() in err.lower():
                            violations[tool] = violations.get(tool, 0) + 1
        return violations

    def _find_repeated_errors(
        self, entries: list[ExperienceEntry]
    ) -> list[tuple[str, int]]:
        """Find errors that occur across multiple tasks."""
        error_counter: dict[str, int] = {}
        for e in entries:
            for err in e.errors:
                # Sanitize: truncate and escape special chars
                key = self._sanitize(err[:80])
                error_counter[key] = error_counter.get(key, 0) + 1
        return [(err, count) for err, count in error_counter.items() if count >= 2]

    def _success_rate_by_type(
        self, entries: list[ExperienceEntry]
    ) -> dict[str, float]:
        """Calculate success rate per task type."""
        by_type: dict[str, list[bool]] = {}
        for e in entries:
            by_type.setdefault(e.task_type, []).append(e.success)
        return {
            t: sum(successes) / len(successes)
            for t, successes in by_type.items()
            if len(successes) >= 2
        }

    @staticmethod
    def _sanitize(text: str) -> str:
        """Sanitize text to prevent prompt injection."""
        text = text.replace("{", "[").replace("}", "]")
        text = text.replace("<", "&lt;").replace(">", "&gt;")
        return text

    @property
    def adjustment_history(self) -> list[dict[str, Any]]:
        return list(self._adjustments)


# ═══════════════════════════════════════════════════════════════════
# Tool Generator
# ═══════════════════════════════════════════════════════════════════

class ToolGenerator:
    """Generates custom Python tools on the fly when the agent needs them."""

    def __init__(self, tools_dir: str | Path | None = None) -> None:
        self.tools_dir = Path(tools_dir) if tools_dir else None
        self._generated_tools: list[dict[str, Any]] = []

    def create_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        python_code: str,
    ) -> dict[str, Any]:
        """Create a new tool from Python code.
        
        The code should define a class that inherits from Tool.
        """
        tool_info = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "code": python_code,
            "created_at": time.time(),
            "usage_count": 0,
            "success_count": 0,
        }
        self._generated_tools.append(tool_info)

        # Save to file if tools_dir is set
        if self.tools_dir:
            self._save_tool_file(name, python_code)

        return tool_info

    def get_tool(self, name: str) -> dict[str, Any] | None:
        """Get a generated tool by name."""
        for tool in self._generated_tools:
            if tool["name"] == name:
                return tool
        return None

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self._generated_tools)

    def record_usage(self, name: str, success: bool) -> None:
        """Record a tool usage for statistics."""
        tool = self.get_tool(name)
        if tool:
            tool["usage_count"] += 1
            if success:
                tool["success_count"] += 1

    def _save_tool_file(self, name: str, code: str) -> None:
        """Save tool code to a Python file."""
        if self.tools_dir:
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            path = self.tools_dir / f"generated_{name}.py"
            path.write_text(code)

    def generate_from_description(
        self, description: str
    ) -> dict[str, Any] | None:
        """Attempt to generate a tool from a natural language description.
        
        In production, this would call an LLM to generate the code.
        For now, it returns None — LLM-driven tool generation
        requires model integration.
        """
        # Placeholder for future LLM-driven tool generation
        return None


# ═══════════════════════════════════════════════════════════════════
# Knowledge Base
# ═══════════════════════════════════════════════════════════════════

class KnowledgeBase:
    """Persistent storage for lessons learned."""

    def __init__(self) -> None:
        self._lessons: dict[str, Any] = {
            "learned_patterns": {},
            "failed_approaches": {},
            "successful_strategies": {},
            "tool_effectiveness": {},
            "file_knowledge": {},  # what we know about specific files
            "user_preferences": {},
            "custom_facts": {},
        }

    def learn(self, category: str, key: str, value: Any) -> None:
        """Add a piece of knowledge."""
        if category in self._lessons:
            self._lessons[category][key] = value

    def recall(self, category: str, key: str, default: Any = None) -> Any:
        """Retrieve a piece of knowledge."""
        if category in self._lessons:
            return self._lessons[category].get(key, default)
        return default

    def get_category(self, category: str) -> dict[str, Any]:
        return dict(self._lessons.get(category, {}))

    def forget(self, category: str, key: str) -> None:
        """Remove a piece of knowledge."""
        if category in self._lessons:
            self._lessons[category].pop(key, None)

    def forget_all(self, category: str) -> None:
        """Clear an entire category."""
        if category in self._lessons:
            self._lessons[category] = {}

    def save(self, path: str | Path) -> int:
        """Save knowledge base to JSON file."""
        Path(path).write_text(json.dumps(self._lessons, indent=2))
        return sum(len(v) for v in self._lessons.values())

    def load(self, path: str | Path) -> int:
        """Load knowledge base from JSON file."""
        if not Path(path).exists():
            return 0
        self._lessons = json.loads(Path(path).read_text())
        return sum(len(v) for v in self._lessons.values())

    def summary(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._lessons.items()}


# ═══════════════════════════════════════════════════════════════════
# Self-Improvement Engine (Main Orchestrator)
# ═══════════════════════════════════════════════════════════════════

class SelfImprovementEngine:
    """Main engine that orchestrates all self-improvement components.
    
    This is what makes Neumann Agent get better over time.
    """

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self._storage = Path(storage_dir) if storage_dir else None

        # Core components
        self.experience_log = ExperienceLog()
        self.pattern_extractor = PatternExtractor(self.experience_log)
        self.strategy_optimizer = StrategyOptimizer(self.experience_log)
        self.prompt_auto_tuner = PromptAutoTuner(self.experience_log)
        self.tool_generator = ToolGenerator(
            tools_dir=self._storage / "generated_tools" if self._storage else None
        )
        self.knowledge_base = KnowledgeBase()

    # ── logging ───────────────────────────────────────────────────

    def log_experience(
        self,
        task: str,
        task_type: str,
        tools_used: list[str],
        tool_sequence: list[str],
        success: bool,
        errors: list[str],
        duration_ms: float,
        iterations: int,
        subtasks_total: int,
        subtasks_completed: int,
        subtasks_failed: int,
        user_feedback: str = "",
    ) -> str:
        """Log a completed task experience."""
        entry = ExperienceEntry(
            id=ExperienceEntry.generate_id(task),
            task=task,
            task_type=task_type,
            tools_used=tools_used,
            tool_sequence=tool_sequence,
            success=success,
            errors=errors,
            duration_ms=duration_ms,
            iterations=iterations,
            subtasks_total=subtasks_total,
            subtasks_completed=subtasks_completed,
            subtasks_failed=subtasks_failed,
            user_feedback=user_feedback,
        )
        self.experience_log.log(entry)

        # Extract lessons if applicable
        if success:
            self._extract_lesson(entry)
        elif errors:
            self._record_failure(entry)

        return entry.id

    # ── querying ──────────────────────────────────────────────────

    def get_insights(self, task_type: str) -> dict[str, Any]:
        """Get actionable insights for a task type."""
        patterns = [
            p.to_dict() for p in self.pattern_extractor.extract_all()
            if task_type in p.applies_to
        ]
        best_strategy = self.strategy_optimizer.recommend(task_type)
        prompt_suggestions = self.prompt_auto_tuner.analyze_and_suggest()

        return {
            "task_type": task_type,
            "total_experiences": self.experience_log.count,
            "overall_success_rate": self.experience_log.success_rate,
            "patterns": patterns,
            "best_strategy": best_strategy.to_dict() if best_strategy else None,
            "prompt_suggestions": prompt_suggestions,
            "knowledge": {
                "successful_strategies": self.knowledge_base.get_category("successful_strategies"),
                "failed_approaches": self.knowledge_base.get_category("failed_approaches"),
            },
        }

    def recommend_strategy(self, task_type: str) -> Strategy:
        """Get the best strategy for a task type."""
        return self.strategy_optimizer.recommend(task_type)

    def get_tool_recommendations(self, task_type: str) -> list[str]:
        """Get recommended tool sequence for a task type."""
        strategy = self.strategy_optimizer.recommend(task_type)
        if strategy:
            return strategy.tool_sequence
        return []

    # ── persistence ───────────────────────────────────────────────

    def save(self, path: str | Path | None = None) -> dict[str, int]:
        """Save all self-improvement data."""
        save_path = Path(path) if path else (self._storage / "self_improve.json" if self._storage else None)
        if not save_path:
            return {"status": 0}  # no storage configured

        data = {
            "experience_log": [e.to_dict() for e in self.experience_log.get_all()],
            "knowledge_base": self.knowledge_base._lessons,
            "generated_tools": self.tool_generator.list_tools(),
            "prompt_adjustments": self.prompt_auto_tuner.adjustment_history,
        }
        save_path.write_text(json.dumps(data, indent=2))
        return {
            "experiences": len(data["experience_log"]),
            "knowledge_entries": sum(len(v) for v in data["knowledge_base"].values()),
            "generated_tools": len(data["generated_tools"]),
        }

    def load(self, path: str | Path | None = None) -> dict[str, int]:
        """Load self-improvement data."""
        load_path = Path(path) if path else (self._storage / "self_improve.json" if self._storage else None)
        if not load_path or not load_path.exists():
            return {"status": 0}

        data = json.loads(load_path.read_text())

        # Restore experience log
        self.experience_log._entries = [
            ExperienceEntry(**e) for e in data.get("experience_log", [])
        ]

        # Restore knowledge base
        kb_data = data.get("knowledge_base", {})
        for cat, items in kb_data.items():
            self.knowledge_base._lessons[cat] = items

        # Rebuild strategy cache
        self.strategy_optimizer._rebuild_cache()

        return {
            "experiences": len(self.experience_log.get_all()),
            "knowledge_entries": sum(len(v) for v in self.knowledge_base._lessons.values()),
        }

    # ── introspection ─────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Get engine statistics."""
        return {
            "total_experiences": self.experience_log.count,
            "success_rate": f"{self.experience_log.success_rate:.1%}",
            "patterns_found": len(self.pattern_extractor.extract_all()),
            "strategies_learned": self.strategy_optimizer.total_strategies,
            "generated_tools": len(self.tool_generator.list_tools()),
            "knowledge_entries": self.knowledge_base.summary(),
            "prompt_adjustments": len(self.prompt_auto_tuner.adjustment_history),
        }

    def reset(self) -> None:
        """Reset all self-improvement data."""
        self.experience_log = ExperienceLog()
        self.pattern_extractor = PatternExtractor(self.experience_log)
        self.strategy_optimizer = StrategyOptimizer(self.experience_log)
        self.prompt_auto_tuner = PromptAutoTuner(self.experience_log)
        self.tool_generator = ToolGenerator()
        self.knowledge_base = KnowledgeBase()

    # ── private ───────────────────────────────────────────────────

    def _extract_lesson(self, entry: ExperienceEntry) -> None:
        """Extract lessons from a successful experience."""
        if entry.tool_sequence:
            key = f"{entry.task_type}_{'_'.join(entry.tool_sequence[:3])}"
            current = self.knowledge_base.recall("successful_strategies", key, [])
            if isinstance(current, list):
                current.append(entry.task[:100])
                self.knowledge_base.learn("successful_strategies", key, current[-5:])  # keep last 5

    def _record_failure(self, entry: ExperienceEntry) -> None:
        """Record failure patterns."""
        for error in entry.errors:
            key = error[:80]
            current = self.knowledge_base.recall("failed_approaches", key, [])
            if isinstance(current, list):
                current.append({"task": entry.task[:100], "tools": entry.tools_used})
                self.knowledge_base.learn("failed_approaches", key, current[-10:])
