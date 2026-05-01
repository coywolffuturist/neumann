"""Tests for Recursive Self-Improvement Engine."""
import json
import tempfile
from pathlib import Path

import pytest
from neumann import (
    SelfImprovementEngine, KnowledgeBase, ToolGenerator,
    ExperienceLog, PatternExtractor, StrategyOptimizer, PromptAutoTuner,
    ExperienceEntry, Pattern, Strategy,
)


# ═══════════════════════════════════════════════════════════════════
# Experience Entry
# ═══════════════════════════════════════════════════════════════════

class TestExperienceEntry:
    def test_generate_id(self):
        id1 = ExperienceEntry.generate_id("test task")
        id2 = ExperienceEntry.generate_id("test task")
        assert len(id1) == 12
        assert id1 != id2  # timestamp makes it unique

    def test_to_dict(self):
        entry = ExperienceEntry(
            id="abc123",
            task="fix bug",
            task_type="debug",
            tools_used=["read_file", "edit_file"],
            tool_sequence=["read_file", "edit_file"],
            success=True,
            errors=[],
            duration_ms=5000.0,
            iterations=3,
            subtasks_total=2,
            subtasks_completed=2,
            subtasks_failed=0,
        )
        d = entry.to_dict()
        assert d["id"] == "abc123"
        assert d["success"] is True
        assert d["tools_used"] == ["read_file", "edit_file"]


# ═══════════════════════════════════════════════════════════════════
# Experience Log
# ═══════════════════════════════════════════════════════════════════

class TestExperienceLog:
    def test_log_and_count(self):
        log = ExperienceLog()
        log.log(ExperienceEntry(
            id="1", task="t1", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        ))
        assert log.count == 1

    def test_filter_by_task_type(self):
        log = ExperienceLog()
        log.log(ExperienceEntry(
            id="1", task="t1", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        ))
        log.log(ExperienceEntry(
            id="2", task="t2", task_type="git",
            tools_used=["git"], tool_sequence=["git"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        ))
        debug_entries = log.get_entries(task_type="debug")
        assert len(debug_entries) == 1

    def test_filter_by_success(self):
        log = ExperienceLog()
        log.log(ExperienceEntry(
            id="1", task="t1", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        ))
        log.log(ExperienceEntry(
            id="2", task="t2", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=False, errors=["error"], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=0, subtasks_failed=1,
        ))
        failures = log.get_entries(task_type="debug", success=False)
        assert len(failures) == 1

    def test_success_rate(self):
        log = ExperienceLog()
        log.log(ExperienceEntry(
            id="1", task="t1", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        ))
        log.log(ExperienceEntry(
            id="2", task="t2", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=False, errors=["err"], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=0, subtasks_failed=1,
        ))
        assert log.success_rate == 0.5

    def test_max_entries(self):
        log = ExperienceLog(max_entries=3)
        for i in range(5):
            log.log(ExperienceEntry(
                id=str(i), task=f"t{i}", task_type="debug",
                tools_used=["read"], tool_sequence=["read"],
                success=True, errors=[], duration_ms=100,
                iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
            ))
        assert log.count == 3

    def test_save_and_load(self, tmp_path):
        log = ExperienceLog()
        log.log(ExperienceEntry(
            id="1", task="t1", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        ))
        f = tmp_path / "exp.json"
        log.save(str(f))
        log2 = ExperienceLog()
        log2.load(str(f))
        assert log2.count == 1


# ═══════════════════════════════════════════════════════════════════
# Pattern Extractor
# ═══════════════════════════════════════════════════════════════════

class TestPatternExtractor:
    def _make_log(self):
        log = ExperienceLog()
        for i in range(5):
            log.log(ExperienceEntry(
                id=str(i), task=f"t{i}", task_type="debug",
                tools_used=["read_file", "edit_file"],
                tool_sequence=["read_file", "edit_file"],
                success=True, errors=[], duration_ms=1000 + i * 100,
                iterations=1, subtasks_total=2, subtasks_completed=2, subtasks_failed=0,
            ))
        for i in range(2):
            log.log(ExperienceEntry(
                id=f"fail{i}", task=f"fail{i}", task_type="debug",
                tools_used=["edit_file"],
                tool_sequence=["edit_file"],
                success=False, errors=["String not found"], duration_ms=500,
                iterations=1, subtasks_total=1, subtasks_completed=0, subtasks_failed=1,
            ))
        return log

    def test_extract_tool_patterns(self):
        log = self._make_log()
        extractor = PatternExtractor(log)
        patterns = extractor.extract_all()
        assert len(patterns) > 0

    def test_extract_error_patterns(self):
        log = self._make_log()
        extractor = PatternExtractor(log)
        patterns = extractor.extract_all()
        # Should find the repeated "String not found" error
        error_patterns = [p for p in patterns if p.pattern_type == "failure"]
        assert len(error_patterns) > 0

    def test_no_patterns_with_little_data(self):
        log = ExperienceLog()
        extractor = PatternExtractor(log)
        patterns = extractor.extract_all()
        assert patterns == []


# ═══════════════════════════════════════════════════════════════════
# Strategy Optimizer
# ═══════════════════════════════════════════════════════════════════

class TestStrategyOptimizer:
    def test_default_strategy(self):
        log = ExperienceLog()
        opt = StrategyOptimizer(log)
        strategy = opt.recommend("debug")
        assert strategy is not None
        assert strategy.task_type == "debug"
        assert "read_file" in strategy.tool_sequence

    def test_learned_strategy(self):
        log = ExperienceLog()
        for i in range(4):
            log.log(ExperienceEntry(
                id=str(i), task=f"t{i}", task_type="debug",
                tools_used=["read_file", "edit_file"],
                tool_sequence=["read_file", "edit_file"],
                success=True, errors=[], duration_ms=1000,
                iterations=1, subtasks_total=2, subtasks_completed=2, subtasks_failed=0,
            ))
        opt = StrategyOptimizer(log)
        strategy = opt.recommend("debug")
        assert strategy is not None
        assert strategy.success_rate == 1.0

    def test_total_strategies(self):
        log = ExperienceLog()
        opt = StrategyOptimizer(log)
        assert opt.total_strategies == 0


# ═══════════════════════════════════════════════════════════════════
# Prompt Auto-Tuner
# ═══════════════════════════════════════════════════════════════════

class TestPromptAutoTuner:
    def test_no_suggestions_with_little_data(self):
        log = ExperienceLog()
        tuner = PromptAutoTuner(log)
        suggestions = tuner.analyze_and_suggest()
        assert suggestions == []

    def test_suggests_on_low_success_rate(self):
        log = ExperienceLog()
        for i in range(5):
            log.log(ExperienceEntry(
                id=str(i), task=f"t{i}", task_type="review",
                tools_used=["read_file"],
                tool_sequence=["read_file"],
                success=False, errors=["task too hard"], duration_ms=1000,
                iterations=1, subtasks_total=1, subtasks_completed=0, subtasks_failed=1,
            ))
        tuner = PromptAutoTuner(log)
        suggestions = tuner.analyze_and_suggest()
        low_rate = [s for s in suggestions if s["type"] == "low_success_rate"]
        assert len(low_rate) > 0

    def test_apply_suggestions(self):
        tuner = PromptAutoTuner(ExperienceLog())
        current = "You are a helpful assistant."
        suggestions = [
            {"type": "reinforce_tool_usage", "suggestion": "Use read_file first."}
        ]
        modified = tuner.apply_suggestions(current, suggestions)
        assert "⚠️" in modified


# ═══════════════════════════════════════════════════════════════════
# Tool Generator
# ═══════════════════════════════════════════════════════════════════

class TestToolGenerator:
    def test_create_tool(self):
        gen = ToolGenerator()
        tool = gen.create_tool(
            name="my_tool",
            description="A custom tool",
            parameters={"type": "object"},
            python_code="class MyTool: pass",
        )
        assert tool["name"] == "my_tool"
        assert tool["usage_count"] == 0

    def test_list_tools(self):
        gen = ToolGenerator()
        gen.create_tool("t1", "desc", {}, "code")
        gen.create_tool("t2", "desc", {}, "code")
        assert len(gen.list_tools()) == 2

    def test_get_tool(self):
        gen = ToolGenerator()
        gen.create_tool("my_tool", "desc", {}, "code")
        tool = gen.get_tool("my_tool")
        assert tool is not None
        assert tool["name"] == "my_tool"

    def test_record_usage(self):
        gen = ToolGenerator()
        gen.create_tool("my_tool", "desc", {}, "code")
        gen.record_usage("my_tool", success=True)
        tool = gen.get_tool("my_tool")
        assert tool["usage_count"] == 1
        assert tool["success_count"] == 1

    def test_save_to_file(self, tmp_path):
        gen = ToolGenerator(tools_dir=str(tmp_path / "tools"))
        gen.create_tool("my_tool", "desc", {}, "class MyTool: pass")
        assert (tmp_path / "tools" / "generated_my_tool.py").exists()


# ═══════════════════════════════════════════════════════════════════
# Knowledge Base
# ═══════════════════════════════════════════════════════════════════

class TestKnowledgeBase:
    def test_learn_and_recall(self):
        kb = KnowledgeBase()
        kb.learn("learned_patterns", "always_read_first", True)
        assert kb.recall("learned_patterns", "always_read_first") is True

    def test_default_value(self):
        kb = KnowledgeBase()
        assert kb.recall("nonexistent", "key", "default") == "default"

    def test_forget(self):
        kb = KnowledgeBase()
        kb.learn("failed_approaches", "bad_idea", True)
        kb.forget("failed_approaches", "bad_idea")
        assert kb.recall("failed_approaches", "bad_idea") is None

    def test_forget_all(self):
        kb = KnowledgeBase()
        kb.learn("tool_effectiveness", "t1", True)
        kb.learn("tool_effectiveness", "t2", True)
        kb.forget_all("tool_effectiveness")
        assert kb.get_category("tool_effectiveness") == {}

    def test_save_and_load(self, tmp_path):
        kb = KnowledgeBase()
        kb.learn("custom_facts", "fact1", "value1")
        f = tmp_path / "kb.json"
        kb.save(str(f))
        kb2 = KnowledgeBase()
        kb2.load(str(f))
        assert kb2.recall("custom_facts", "fact1") == "value1"

    def test_summary(self):
        kb = KnowledgeBase()
        kb.learn("learned_patterns", "p1", True)
        kb.learn("failed_approaches", "f1", True)
        s = kb.summary()
        assert s["learned_patterns"] == 1
        assert s["failed_approaches"] == 1


# ═══════════════════════════════════════════════════════════════════
# Self-Improvement Engine (Integration)
# ═══════════════════════════════════════════════════════════════════

class TestSelfImprovementEngine:
    def test_log_experience(self):
        engine = SelfImprovementEngine()
        engine.log_experience(
            task="fix bug in main.py",
            task_type="debug",
            tools_used=["read_file", "edit_file"],
            tool_sequence=["read_file", "edit_file"],
            success=True,
            errors=[],
            duration_ms=5000.0,
            iterations=3,
            subtasks_total=2,
            subtasks_completed=2,
            subtasks_failed=0,
        )
        assert engine.experience_log.count == 1
        assert engine.experience_log.success_rate == 1.0

    def test_get_insights(self):
        engine = SelfImprovementEngine()
        for i in range(5):
            engine.log_experience(
                task=f"fix bug {i}",
                task_type="debug",
                tools_used=["read_file", "edit_file"],
                tool_sequence=["read_file", "edit_file"],
                success=True, errors=[], duration_ms=1000,
                iterations=1, subtasks_total=2, subtasks_completed=2, subtasks_failed=0,
            )
        insights = engine.get_insights("debug")
        assert insights["total_experiences"] == 5
        assert insights["overall_success_rate"] == 1.0

    def test_recommend_strategy(self):
        engine = SelfImprovementEngine()
        for i in range(3):
            engine.log_experience(
                task=f"task {i}",
                task_type="debug",
                tools_used=["read_file", "edit_file"],
                tool_sequence=["read_file", "edit_file"],
                success=True, errors=[], duration_ms=1000,
                iterations=1, subtasks_total=2, subtasks_completed=2, subtasks_failed=0,
            )
        strategy = engine.recommend_strategy("debug")
        assert strategy is not None

    def test_tool_recommendations(self):
        engine = SelfImprovementEngine()
        for i in range(3):
            engine.log_experience(
                task=f"task {i}",
                task_type="debug",
                tools_used=["read_file", "edit_file"],
                tool_sequence=["read_file", "edit_file"],
                success=True, errors=[], duration_ms=1000,
                iterations=1, subtasks_total=2, subtasks_completed=2, subtasks_failed=0,
            )
        recs = engine.get_tool_recommendations("debug")
        assert "read_file" in recs

    def test_save_and_load(self, tmp_path):
        engine = SelfImprovementEngine()
        engine.log_experience(
            task="test task",
            task_type="debug",
            tools_used=["read_file"],
            tool_sequence=["read_file"],
            success=True, errors=[], duration_ms=500,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        )
        f = tmp_path / "self_improve.json"
        engine.save(str(f))

        engine2 = SelfImprovementEngine()
        engine2.load(str(f))
        assert engine2.experience_log.count == 1

    def test_stats(self):
        engine = SelfImprovementEngine()
        engine.log_experience(
            task="t1", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        )
        stats = engine.stats()
        assert "total_experiences" in stats
        assert "success_rate" in stats
        assert "patterns_found" in stats

    def test_reset(self):
        engine = SelfImprovementEngine()
        engine.log_experience(
            task="t1", task_type="debug",
            tools_used=["read"], tool_sequence=["read"],
            success=True, errors=[], duration_ms=100,
            iterations=1, subtasks_total=1, subtasks_completed=1, subtasks_failed=0,
        )
        engine.reset()
        assert engine.experience_log.count == 0
