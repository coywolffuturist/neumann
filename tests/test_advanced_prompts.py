"""Tests for AdvancedPromptEngine — multi-layer, persona-driven prompts."""
import pytest
from neumann import AdvancedPromptEngine, PromptContext, RenderedPrompt


class TestTaskDetection:
    def test_detect_debug(self):
        engine = AdvancedPromptEngine()
        assert engine._detect_task_type("fix this bug, it crashes on startup") == "debug"
        assert engine._detect_task_type("error: null pointer exception") == "debug"
        assert engine._detect_task_type("not working, traceback shows IndexError") == "debug"

    def test_detect_review(self):
        engine = AdvancedPromptEngine()
        assert engine._detect_task_type("review this code please") == "review"
        assert engine._detect_task_type("check for quality issues") == "review"

    def test_detect_refactor(self):
        engine = AdvancedPromptEngine()
        assert engine._detect_task_type("refactor this function") == "refactor"
        assert engine._detect_task_type("clean up this mess") == "refactor"

    def test_detect_explain(self):
        engine = AdvancedPromptEngine()
        assert engine._detect_task_type("explain this code") == "explain"
        assert engine._detect_task_type("what does this function do") == "explain"

    def test_detect_git(self):
        engine = AdvancedPromptEngine()
        assert engine._detect_task_type("commit these changes") == "git"
        assert engine._detect_task_type("git push origin main") == "git"

    def test_detect_general(self):
        engine = AdvancedPromptEngine()
        assert engine._detect_task_type("hello") == "general"
        assert engine._detect_task_type("hi there") == "general"


class TestPersonaSelection:
    def test_select_debugger_persona(self):
        engine = AdvancedPromptEngine()
        persona = engine._select_persona("debug")
        assert "Debugger" in persona

    def test_select_reviewer_persona(self):
        engine = AdvancedPromptEngine()
        persona = engine._select_persona("review")
        assert "Reviewer" in persona

    def test_select_general_persona(self):
        engine = AdvancedPromptEngine()
        persona = engine._select_persona("general")
        assert "General Assistant" in persona


class TestBuildPrompt:
    def test_build_complete_prompt(self):
        engine = AdvancedPromptEngine()
        result = engine.build_prompt("fix this bug: TypeError on line 42")
        assert isinstance(result, RenderedPrompt)
        assert result.task_type == "debug"
        assert "Current Mode: Debugger" in result.persona
        assert len(result.system_prompt) > 500  # Multi-layer prompt

    def test_build_prompt_with_file_context(self):
        engine = AdvancedPromptEngine()
        ctx = PromptContext(
            user_input="fix the bug",
            file_path="main.py",
            file_content='def foo():\n    return 1/0',
            error_message="ZeroDivisionError",
        )
        result = engine.build_prompt("fix the bug", context=ctx)
        assert "main.py" in result.system_prompt
        # Error is in context section, not necessarily system prompt
        assert result.metadata["has_file_context"] is True

    def test_build_prompt_with_git_context(self):
        engine = AdvancedPromptEngine()
        ctx = PromptContext(
            user_input="commit changes",
            git_status="Branch: main\nModified: main.py",
        )
        result = engine.build_prompt("commit changes", context=ctx)
        assert "Git Status" in result.system_prompt

    def test_build_prompt_includes_user_preferences(self):
        engine = AdvancedPromptEngine()
        engine.set_user_preferences({"language": "TypeScript", "style": "concise"})
        result = engine.build_prompt("write a sorting function")
        assert "TypeScript" in result.system_prompt
        assert "concise" in result.system_prompt

    def test_build_prompt_includes_tool_awareness(self):
        engine = AdvancedPromptEngine()
        result = engine.build_prompt("read main.py and fix the bug")
        assert "Available Tools" in result.system_prompt
        assert "read_file" in result.system_prompt
        assert "edit_file" in result.system_prompt

    def test_build_prompt_includes_output_format(self):
        engine = AdvancedPromptEngine()
        result = engine.build_prompt("hello")
        assert "Output Rules" in result.system_prompt


class TestConversationPrompt:
    def test_build_conversation_prompt(self):
        engine = AdvancedPromptEngine()
        history = [
            {"role": "user", "content": "write a function"},
            {"role": "assistant", "content": "here it is: ..."},
        ]
        result = engine.build_conversation_prompt("now add tests", history=history)
        assert isinstance(result, RenderedPrompt)
        assert "Conversation Context" in result.system_prompt
        assert result.metadata["turns"] == 2


class TestSelfCorrectionPrompt:
    def test_build_self_correction_prompt(self):
        engine = AdvancedPromptEngine()
        result = engine.build_self_correction_prompt(
            original_attempt="def foo(): return 1/0",
            error_feedback="ZeroDivisionError: division by zero",
        )
        assert "Self-Correction Mode" in result.system_prompt
        assert "Previous Attempt" in result.user_prompt
        assert "Error Feedback" in result.user_prompt


class TestCustomization:
    def test_register_custom_persona(self):
        engine = AdvancedPromptEngine()
        engine.register_persona("security", "You are a security expert.")
        persona = engine._select_persona("security")
        # Custom persona won't be auto-detected, but it's registered
        assert engine._personas["security"] == "You are a security expert."

    def test_register_custom_task_template(self):
        engine = AdvancedPromptEngine()
        engine.register_task_template("deploy", "Deploy to {{environment}}")
        assert "deploy" in engine._task_templates


class TestStats:
    def test_stats(self):
        engine = AdvancedPromptEngine()
        engine.build_prompt("fix this bug")
        engine.build_prompt("review my code")
        s = engine.stats()
        assert s["total_tasks"] == 2
        assert "debug" in s["task_distribution"]
        assert "review" in s["task_distribution"]

    def test_task_history(self):
        engine = AdvancedPromptEngine()
        engine.build_prompt("fix this TypeError")
        history = engine.get_task_history()
        assert len(history) == 1
        assert history[0]["type"] == "debug"

    def test_clear_history(self):
        engine = AdvancedPromptEngine()
        engine.build_prompt("fix this")
        engine.clear_task_history()
        assert engine.get_task_history() == []


class TestTemplateRendering:
    def test_if_block_present(self):
        engine = AdvancedPromptEngine()
        result = engine._render_template(
            "Hello {{#if name}}{{name}}{{/if}}",
            {"name": "World"},
        )
        assert result == "Hello World"

    def test_if_block_absent(self):
        engine = AdvancedPromptEngine()
        result = engine._render_template(
            "Hello {{#if name}}{{name}}{{/if}}!",
            {"name": ""},
        )
        assert result == "Hello !"

    def test_variable_substitution(self):
        engine = AdvancedPromptEngine()
        result = engine._render_template(
            "File: {{file_path}}, Line: {{line}}",
            {"file_path": "main.py", "line": "42"},
        )
        assert result == "File: main.py, Line: 42"


class TestContextBuilding:
    def test_context_with_file_and_git(self):
        engine = AdvancedPromptEngine()
        ctx = PromptContext(
            file_path="test.py",
            file_content="assert True",
            git_status="clean",
        )
        text = engine._build_context_text(ctx)
        assert "test.py" in text
        assert "assert True" in text
        assert "clean" in text

    def test_context_empty(self):
        engine = AdvancedPromptEngine()
        ctx = PromptContext()
        text = engine._build_context_text(ctx)
        assert text == ""
