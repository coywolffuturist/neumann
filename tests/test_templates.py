"""Tests for PromptTemplateEngine."""
import json
import pytest
from neumann import PromptTemplateEngine, Template


class TestPromptTemplateEngine:
    def test_list_builtins(self):
        engine = PromptTemplateEngine()
        templates = engine.list_templates()
        assert "code_generation" in templates
        assert "bug_fix" in templates
        assert "code_review" in templates
        assert "refactor" in templates
        assert "general_assistant" in templates

    def test_render_code_generation(self):
        engine = PromptTemplateEngine()
        result = engine.render("code_generation", language="TypeScript", task="sort an array")
        assert "TypeScript" in result["system"]
        assert "sort an array" in result["instruction"]

    def test_render_bug_fix(self):
        engine = PromptTemplateEngine()
        result = engine.render(
            "bug_fix",
            language="Python",
            code="x = 1 / 0",
            error="ZeroDivisionError",
        )
        assert "ZeroDivisionError" in result["instruction"]
        assert "x = 1 / 0" in result["instruction"]

    def test_render_code_review(self):
        engine = PromptTemplateEngine()
        result = engine.render("code_review", language="Python", code="import os; os.system(cmd)")
        assert "import os; os.system(cmd)" in result["instruction"]

    def test_render_with_defaults(self):
        engine = PromptTemplateEngine()
        # language has default value "Python"
        result = engine.render("code_generation", task="read a file")
        assert "Python" in result["system"]
        assert "read a file" in result["instruction"]

    def test_render_conditional_block_present(self):
        engine = PromptTemplateEngine()
        result = engine.render(
            "code_generation",
            task="sort",
            requirements="must be O(n log n)",
        )
        assert "must be O(n log n)" in result["instruction"]

    def test_render_conditional_block_absent(self):
        engine = PromptTemplateEngine()
        result = engine.render("code_generation", task="sort")
        # The conditional block should be removed when variable is empty
        assert "Requirements:" not in result["instruction"]

    def test_render_simple(self):
        engine = PromptTemplateEngine()
        result = engine.render_simple("general_assistant", prompt="Hello!")
        assert result == "Hello!"

    def test_unknown_template(self):
        engine = PromptTemplateEngine()
        with pytest.raises(KeyError, match="not found"):
            engine.render("nonexistent")

    def test_register_custom_template(self):
        engine = PromptTemplateEngine()
        engine.register(Template(
            name="my_template",
            system="You are a {{role}}.",
            instruction="Do this: {{action}}",
            variables={"role": "helper", "action": "help me"},
        ))
        result = engine.render("my_template")
        assert result["system"] == "You are a helper."
        assert result["instruction"] == "Do this: help me"

    def test_register_override_builtin(self):
        engine = PromptTemplateEngine()
        engine.register(Template(
            name="code_generation",
            system="Custom system",
            instruction="Custom instruction",
        ))
        result = engine.render("code_generation")
        assert result["system"] == "Custom system"

    def test_unregister(self):
        engine = PromptTemplateEngine()
        engine.unregister("general_assistant")
        assert "general_assistant" not in engine.list_templates()

    def test_save_and_load(self, tmp_path):
        engine = PromptTemplateEngine()
        # Register a custom template
        engine.register(Template(
            name="custom",
            system="System: {{topic}}",
            instruction="Task: {{task}}",
            variables={"topic": "default", "task": "nothing"},
        ))

        f = tmp_path / "templates.json"
        count = engine.save_templates(str(f), names=["custom"])
        assert count == 1

        engine2 = PromptTemplateEngine()
        loaded = engine2.load_templates(str(f))
        assert loaded == 1

        result = engine2.render("custom", topic="AI")
        assert "AI" in result["system"]

    def test_save_all_templates(self, tmp_path):
        engine = PromptTemplateEngine()
        f = tmp_path / "all_templates.json"
        count = engine.save_templates(str(f))
        assert count > 5  # Should save all builtins

        data = json.loads(f.read_text())
        assert "code_generation" in data
        assert "bug_fix" in data

    def test_template_render_variable_substitution(self):
        tpl = Template(
            name="test",
            system="Hello {{name}}!",
            instruction="Process: {{task}}",
            variables={"name": "World", "task": "something"},
        )
        result = tpl.render(name="Developer")
        assert result["system"] == "Hello Developer!"
        assert result["instruction"] == "Process: something"
