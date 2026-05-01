"""Prompt Templates — system prompts, instruction templates, and variable injection.

Features:
- Template engine with {{variable}} syntax
- Built-in templates for coding, debugging, reviewing, explaining
- Custom template loading from YAML/JSON files
- Variable injection with fallback defaults

Usage:
    from neumann.templates import PromptTemplateEngine
    
    engine = PromptTemplateEngine()
    prompt = engine.render("code_generation", language="Python", task="sort a list")
    
    # Load custom templates
    engine.load_templates("my_templates.json")
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Built-in templates ──────────────────────────────────────────────

_BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "code_generation": {
        "system": (
            "You are an expert {{language}} developer. "
            "Write clean, well-documented code. "
            "Follow best practices for {{language}}. "
            "Include type hints and docstrings where appropriate."
        ),
        "instruction": (
            "Write {{language}} code to {{task}}.\n"
            "{% if requirements %}Requirements: {{requirements}}\n{% endif %}"
            "{% if constraints %}Constraints: {{constraints}}\n{% endif %}"
            "Provide only the code, with brief explanations."
        ),
        "variables": {
            "language": "Python",
            "task": "",
            "requirements": "",
            "constraints": "",
        },
    },
    "code_explanation": {
        "system": (
            "You are a patient code reviewer. "
            "Explain code clearly and concisely. "
            "Focus on what the code does, why it works, and any potential issues."
        ),
        "instruction": (
            "Explain this {{language}} code:\n\n"
            "```\n{{code}}\n```\n\n"
            "Include:\n"
            "1. What it does (1-2 sentences)\n"
            "2. How it works (key logic)\n"
            "3. Any issues or improvements"
        ),
        "variables": {
            "language": "Python",
            "code": "",
        },
    },
    "bug_fix": {
        "system": (
            "You are a debugger expert. "
            "Find the root cause of bugs and provide minimal, correct fixes. "
            "Explain why the bug occurs and why the fix works."
        ),
        "instruction": (
            "Fix this bug in {{language}} code:\n\n"
            "```\n{{code}}\n```\n\n"
            "Error message: {{error}}\n\n"
            "{% if expected_behavior %}Expected behavior: {{expected_behavior}}\n{% endif %}"
            "Provide:\n"
            "1. Root cause\n"
            "2. Fixed code\n"
            "3. Explanation"
        ),
        "variables": {
            "language": "Python",
            "code": "",
            "error": "",
            "expected_behavior": "",
        },
    },
    "code_review": {
        "system": (
            "You are a senior code reviewer. "
            "Review for correctness, security, performance, and readability. "
            "Be constructive and specific."
        ),
        "instruction": (
            "Review this {{language}} code:\n\n"
            "```\n{{code}}\n```\n\n"
            "Check for:\n"
            "1. Correctness (bugs, edge cases)\n"
            "2. Security (injections, leaks, exposures)\n"
            "3. Performance (complexity, allocations)\n"
            "4. Readability (naming, structure, comments)\n\n"
            "Provide specific line-level feedback."
        ),
        "variables": {
            "language": "Python",
            "code": "",
        },
    },
    "refactor": {
        "system": (
            "You are a refactoring expert. "
            "Improve code structure without changing behavior. "
            "Follow SOLID principles and design patterns."
        ),
        "instruction": (
            "Refactor this {{language}} code:\n\n"
            "```\n{{code}}\n```\n\n"
            "{% if goal %}Refactoring goal: {{goal}}\n{% endif %}"
            "Provide:\n"
            "1. What you changed and why\n"
            "2. The refactored code\n"
            "3. Benefits of the new structure"
        ),
        "variables": {
            "language": "Python",
            "code": "",
            "goal": "Improve readability and reduce duplication",
        },
    },
    "general_assistant": {
        "system": (
            "You are a skilled coding assistant. "
            "Write clean, correct, and well-documented code. "
            "Explain your reasoning concisely."
        ),
        "instruction": "{{prompt}}",
        "variables": {
            "prompt": "",
        },
    },
}


@dataclass
class Template:
    """A single prompt template with variables."""
    name: str
    system: str
    instruction: str
    variables: dict[str, Any] = field(default_factory=dict)

    def render(self, **overrides: Any) -> dict[str, str]:
        """Render the template with variables."""
        ctx = {**self.variables, **overrides}
        system = _render_template(self.system, ctx)
        instruction = _render_template(self.instruction, ctx)
        return {"system": system, "instruction": instruction}


class PromptTemplateEngine:
    """Manages prompt templates with variable injection."""

    def __init__(self) -> None:
        self._templates: dict[str, Template] = {}
        self._load_builtins()

    # ── registration ──────────────────────────────────────────────

    def register(self, template: Template) -> None:
        """Register a template."""
        self._templates[template.name] = template

    def unregister(self, name: str) -> None:
        """Remove a template."""
        self._templates.pop(name, None)

    def list_templates(self) -> list[str]:
        """List all available template names."""
        return sorted(self._templates.keys())

    def get_template(self, name: str) -> Template | None:
        """Get a template by name."""
        return self._templates.get(name)

    # ── rendering ─────────────────────────────────────────────────

    def render(self, name: str, **variables: Any) -> dict[str, str]:
        """Render a template by name with variable overrides.
        
        Returns dict with 'system' and 'instruction' keys.
        """
        template = self.get_template(name)
        if template is None:
            raise KeyError(f"Template not found: {name}")
        return template.render(**variables)

    def render_simple(
        self, name: str, **variables: Any
    ) -> str:
        """Render a template and return just the instruction."""
        result = self.render(name, **variables)
        return result["instruction"]

    # ── loading ───────────────────────────────────────────────────

    def load_templates(self, path: str | Path) -> int:
        """Load templates from a JSON file.
        
        File format:
        {
            "template_name": {
                "system": "...",
                "instruction": "...",
                "variables": {"var1": "default1"}
            }
        }
        
        Returns the number of templates loaded.
        """
        data = json.loads(Path(path).read_text())
        count = 0
        for name, tpl in data.items():
            self._templates[name] = Template(
                name=name,
                system=tpl.get("system", ""),
                instruction=tpl.get("instruction", ""),
                variables=tpl.get("variables", {}),
            )
            count += 1
        return count

    def save_templates(self, path: str | Path, names: list[str] | None = None) -> int:
        """Save templates to a JSON file.
        
        If names is None, saves all templates.
        """
        templates_to_save = {}
        for name in (names or self._templates.keys()):
            tpl = self._templates.get(name)
            if tpl:
                templates_to_save[name] = {
                    "system": tpl.system,
                    "instruction": tpl.instruction,
                    "variables": tpl.variables,
                }
        Path(path).write_text(json.dumps(templates_to_save, indent=2))
        return len(templates_to_save)

    # ── private ───────────────────────────────────────────────────

    def _load_builtins(self) -> None:
        for name, data in _BUILTIN_TEMPLATES.items():
            self._templates[name] = Template(
                name=name,
                system=data["system"],
                instruction=data["instruction"],
                variables=data.get("variables", {}),
            )


# ── Template rendering engine ──────────────────────────────────────

def _render_template(text: str, variables: dict[str, Any]) -> str:
    """Render a template string with variable substitution and conditional blocks."""
    result = text

    # Handle {% if var %}...{% endif %} blocks
    if_pattern = re.compile(r'\{% if (\w+) %\}(.*?)\{% endif %\}', re.DOTALL)
    while True:
        match = if_pattern.search(result)
        if not match:
            break
        var_name = match.group(1)
        block_content = match.group(2)
        var_value = variables.get(var_name, "")
        if var_value:
            # Also render variables inside the block
            rendered = _render_simple_vars(block_content, variables)
            result = result[:match.start()] + rendered + result[match.end():]
        else:
            result = result[:match.start()] + "" + result[match.end():]

    # Handle {{variable}} substitutions
    result = _render_simple_vars(result, variables)
    return result


def _render_simple_vars(text: str, variables: dict[str, Any]) -> str:
    """Replace {{variable}} with values."""
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return str(variables.get(var_name, match.group(0)))

    return re.sub(r'\{\{(\w+)\}\}', replacer, text)
