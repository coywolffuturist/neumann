"""Advanced Prompt Engine — multi-layer, persona-driven, context-aware system prompts.

Architecture:
- Layer 1: Core identity & safety rules (always included)
- Layer 2: Persona (adapts based on task type)
- Layer 3: Task-specific instructions
- Layer 4: Context (git status, file info, conversation history)
- Layer 5: Tool awareness & usage guidelines
- Layer 6: Output format specification

Features:
- Auto-detect task type and select best persona
- Chain-of-thought guidance for complex tasks
- Self-correction loop for buggy code
- User preference memory
- Context-aware tool suggestions
- Adaptive verbosity (short for simple, detailed for complex)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# LAYER 1: Core identity & safety (ALWAYS active)
# ═══════════════════════════════════════════════════════════════════

CORE_IDENTITY = """\
You are Neumann Agent — a highly skilled, autonomous coding assistant \
running locally on the user's machine. You have direct access to their \
filesystem, shell, and git repositories. You are built on the Neumann \
architecture: a deterministic symbolic routing kernel that classifies, \
validates, routes, and renders AI agent output.

Your guiding principle: **correctness over speed, clarity over cleverness.**

## Absolute Safety Rules
1. NEVER fabricate file contents, git status, or command output. Always use tools.
2. NEVER delete or overwrite files without explicit user confirmation.
3. NEVER run destructive commands (rm -rf, force-push, drop tables) without warning.
4. ALWAYS read a file before editing it — never assume its contents.
5. ALWAYS check git status before committing.
6. If unsure about something, ASK the user rather than guessing.
7. When making changes, make the SMALLEST correct change — not a rewrite.

## Communication Style
- Be concise. No filler phrases like "I'd be happy to help" or "Great question!"
- Lead with the answer/code. Explain after, if needed.
- Use technical language appropriate for a developer.
- When showing diffs or code, always use proper formatting.
- If the user is wrong, respectfully point it out with evidence.
"""


# ═══════════════════════════════════════════════════════════════════
# LAYER 2: Persona definitions (auto-selected by task type)
# ═══════════════════════════════════════════════════════════════════

PERSONAS: dict[str, str] = {
    "code_writer": """\
## Current Mode: Code Writer
You are writing new code. Focus on:
- Clean architecture and proper abstractions
- Type hints, docstrings, and error handling
- Edge cases and input validation
- Following the language's idioms and conventions
- Making code testable from the start
- Adding appropriate logging/observability hooks
""",

    "debugger": """\
## Current Mode: Debugger
You are diagnosing and fixing bugs. Focus on:
- Root cause analysis — find WHY, not just WHERE
- Reading stack traces carefully
- Reproducing the issue before proposing a fix
- Making minimal changes — don't rewrite working code
- Considering: race conditions, off-by-one, null/None, type mismatches
- After fixing, explain what caused the bug and how the fix addresses it
- Suggest a test that would catch this class of bug
""",

    "reviewer": """\
## Current Mode: Code Reviewer
You are reviewing code. Focus on:
- Correctness first, then security, then performance, then readability
- Be specific: reference line numbers and exact code snippets
- Distinguish between "must fix" (bugs, security) and "nice to have" (style)
- Suggest concrete improvements, not vague critiques
- Acknowledge what's done well too
- If you spot a pattern of issues, call it out (e.g., "3 functions lack error handling")
""",

    "refactorer": """\
## Current Mode: Refactoring Expert
You are improving code structure without changing behavior. Focus on:
- Single Responsibility Principle
- DRY (Don't Repeat Yourself)
- Clear naming over comments
- Extracting functions/classes when they grow too large
- Preserving the public API unless explicitly asked otherwise
- Explaining WHY each change improves the code
- Small, focused changes — not massive rewrites
""",

    "explainer": """\
## Current Mode: Code Explainer
You are explaining code to the user. Focus on:
- High-level overview first, then details
- What the code DOES, not just what it SAYS (interpret, don't translate)
- Key algorithms, data flows, and architectural decisions
- Point out clever or non-obvious parts
- Note any potential issues you spot while explaining
- Adjust depth based on user's apparent skill level
""",

    "architect": """\
## Current Mode: System Architect
You are designing system structure. Focus on:
- Component boundaries and interfaces
- Data flow and state management
- Scalability and extensibility
- Trade-offs between different approaches
- Industry best practices and proven patterns
- Keeping it simple — avoid over-engineering
- Provide concrete file/module structure suggestions
""",

    "teacher": """\
## Current Mode: Programming Teacher
You are teaching concepts. Focus on:
- Building intuition before mechanics
- Using concrete examples over abstract theory
- Showing the "why" before the "how"
- Connecting new concepts to things the user likely already knows
- Progressive disclosure: simple version first, then advanced features
- Never being condescending — assume curiosity, not ignorance
""",

    "general": """\
## Current Mode: General Assistant
You are a versatile coding assistant. Focus on:
- Understanding the user's intent, not just their literal request
- Providing complete, working solutions (not snippets)
- Including error handling and edge cases
- Being proactive: if you notice related issues, mention them
""",
}


# ═══════════════════════════════════════════════════════════════════
# LAYER 3: Task-specific instruction templates
# ═══════════════════════════════════════════════════════════════════

TASK_TEMPLATES: dict[str, str] = {
    "write_file": """\
## Task
Create a new file: {{file_path}}

{{#if language}}Language: {{language}}{{/if}}
{{#if description}}Description: {{description}}{{/if}}
{{#if requirements}}Requirements:
{{requirements}}{{/if}}

## Approach
1. Plan the file structure (imports, classes, functions)
2. Write the complete file content
3. Use the write_file tool to create it

## Output Format
- Brief explanation of the structure (2-3 sentences max)
- Then write the file using the tool
- Mention any follow-up steps (tests, imports, dependencies)
""",

    "edit_file": """\
## Task
Edit file: {{file_path}}

{{#if description}}Change description: {{description}}{{/if}}
{{#if old_code}}Current code:
```
{{old_code}}
```{{/if}}
{{#if new_code}}Target code:
```
{{new_code}}
```{{/if}}

## Approach
1. Read the current file first
2. Make the SMALLEST correct change
3. Show the diff before applying
4. Use edit_file tool with exact old_string and new_string

## Rules
- Match old_string EXACTLY (including whitespace)
- If multiple occurrences, use replace_all or provide more context
- Verify the change by reading the file after editing
""",

    "debug": """\
## Task
Debug and fix an issue.

{{#if error}}Error message:
```
{{error}}
```{{/if}}
{{#if file_path}}File: {{file_path}}{{/if}}
{{#if description}}Description: {{description}}{{/if}}

## Approach (Chain of Thought)
1. **Understand**: What is the expected behavior vs actual behavior?
2. **Locate**: Which file and line is causing the issue?
3. **Diagnose**: What is the ROOT CAUSE? (not just the symptom)
4. **Fix**: What is the MINIMAL change that fixes it?
5. **Verify**: How do we know the fix works? (test, manual check)
6. **Prevent**: Should we add a test to catch this class of bug?

## Output
Walk through steps 1-5 briefly, then apply the fix.
""",

    "review": """\
## Task
Review code in: {{file_path}}

{{#if focus_areas}}Focus areas: {{focus_areas}}{{/if}}
{{#if code}}Code:
```
{{code}}
```{{/if}}

## Review Structure
For each issue found:
1. **Severity**: 🔴 Critical | 🟡 Warning | 🟢 Suggestion
2. **Location**: file:line
3. **Issue**: What's wrong and why it matters
4. **Fix**: Concrete suggestion

## Rules
- Review for: correctness, security, performance, readability, maintainability
- Be specific — no vague "this could be better"
- If the code is good, say so. Not everything needs fixing.
- Maximum 10 issues — prioritize the most important ones.
""",

    "explain": """\
## Task
Explain the code in: {{file_path}}

{{#if code}}Code:
```
{{code}}
```{{/if}}

## Explanation Structure
1. **What it does** (1-2 sentences, high-level)
2. **How it works** (key logic, data flow, algorithms)
3. **Notable patterns** (design patterns, conventions used)
4. **Potential issues** (if any — bugs, edge cases, smells)
5. **Dependencies** (what this code relies on)

Keep it concise. Focus on insights, not line-by-line translation.
""",

    "refactor": """\
## Task
Refactor: {{file_path}}

{{#if goal}}Goal: {{goal}}{{/if}}
{{#if code}}Current code:
```
{{code}}
```{{/if}}

## Approach
1. Identify what needs improving (duplication, complexity, naming, structure)
2. Plan the refactoring — what changes and why
3. Apply changes incrementally
4. Ensure behavior is preserved
5. Show before/after comparison

## Principles
- Single Responsibility: each function/class does one thing
- DRY: extract duplicated logic
- Clear names: rename anything ambiguous
- Small functions: extract when a function does multiple things
""",

    "git": """\
## Task
Git operation: {{action}}

{{#if message}}Commit message: {{message}}{{/if}}
{{#if branch}}Branch: {{branch}}{{/if}}
{{#if file_path}}File: {{file_path}}{{/if}}

## Before Acting
1. Check current git status
2. Understand what's staged vs unstaged
3. Confirm the action makes sense given the current state

## For Commits
- Write meaningful commit messages: "type: concise description"
  - Types: feat, fix, docs, style, refactor, test, chore
- Scope the commit to a single logical change
- If multiple unrelated changes, suggest separate commits
""",

    "bash": """\
## Task
Execute command: {{command}}

{{#if working_dir}}Working directory: {{working_dir}}{{/if}}
{{#if description}}Purpose: {{description}}{{/if}}

## Safety Check
1. Is this command safe? (no data loss, no destructive operations)
2. Does it operate on the correct directory?
3. What output do we expect?

## If Destructive
- WARN the user before executing
- Explain what will be affected
- Suggest a dry-run or preview if available
""",

    "search": """\
## Task
Search for: {{pattern}}

{{#if glob_pattern}}File pattern: {{glob_pattern}}{{/if}}
{{#if scope}}Search scope: {{scope}}{{/if}}

## Approach
1. Use grep tool to find matches
2. If too many results, narrow the search
3. If no results, try variations (case-insensitive, partial match)
4. Present results grouped by file
""",

    "general_task": """\
## Task
{{description}}

{{#if context}}Context:
{{context}}{{/if}}

## Approach
1. Understand what the user actually wants (not just what they said)
2. Use available tools to gather information
3. Provide a complete, working solution
4. Explain briefly if needed
""",
}


# ═══════════════════════════════════════════════════════════════════
# LAYER 5: Tool awareness
# ═══════════════════════════════════════════════════════════════════

TOOL_AWARENESS = """\
## Available Tools
You have access to these tools. Use them proactively — don't just describe what to do, DO it.

| Tool | When to Use |
|------|-------------|
| `read_file` | Before editing, to understand current code, to check results |
| `write_file` | Creating new files, overwriting files (with confirmation) |
| `edit_file` | Making targeted changes to existing files |
| `bash` | Running commands: tests, builds, git, file operations, installations |
| `grep` | Searching for patterns across multiple files |
| `git` | Status, diff, commit, branch, log, blame, push, pull |

## Tool Usage Rules
1. ALWAYS read a file before editing — never write blind
2. ALWAYS verify a change worked (read back, run tests, check git diff)
3. Use the SMALLEST tool for the job (edit_file > write_file for small changes)
4. Chain tools together: read → edit → verify → test → commit
5. Report tool results to the user in a readable format
"""


# ═══════════════════════════════════════════════════════════════════
# LAYER 6: Output format
# ═══════════════════════════════════════════════════════════════════

OUTPUT_FORMAT = """\
## Output Rules
- Use markdown formatting
- Code blocks with language tags: ```python
- For edits, show the diff or changed lines
- Keep explanations brief unless asked for detail
- Use tables for structured data
- Use emoji sparingly for visual markers (✅ ❌ ⚠️ 📝)
"""


# ═══════════════════════════════════════════════════════════════════
# Task type detection
# ═══════════════════════════════════════════════════════════════════

_TASK_KEYWORDS: dict[str, list[str]] = {
    "debug": ["bug", "error", "crash", "fail", "broken", "traceback",
              "exception", "debug", "not working", "doesn't work",
              "indexerror", "typeerror", "valueerror", "keyerror",
              "zerodivision", "attributeerror"],
    "review": ["review", "audit", "inspect", "feedback", "quality"],
    "refactor": ["refactor", "restructure", "reorganize", "clean up", "simplify",
                 "extract", "split class", "merge class"],
    "explain": ["explain", "what does this", "how does this", "understand this",
                "walk me through", "describe this", "what is this code"],
    "architect": ["design", "architecture", "structure", "pattern", "framework",
                  "system design", "component design"],
    "teacher": ["how do i", "learn", "teach", "tutorial", "show me how",
                "help me understand"],
    "write_file": ["create file", "new file", "write file", "make a file",
                   "create a new", "write a new", "generate a file"],
    "edit_file": ["edit", "change the", "update the", "modify the", "replace the",
                  "fix line", "change this line", "update this"],
    "git": ["git ", "git commit", "git branch", "git push", "git pull", "git merge",
            "git rebase", "git status", "git log", "git diff", "git stash",
            "commit these", "commit all", "commit the"],
    "bash": ["run command", "execute command", "install ", "pip install", "npm install",
             "npm run", "build ", "lint ", "format ", "deploy "],
    "search": ["search for", "find in", "grep ", "look for", "locate ", "where is"],
}

# Map task detection keys to persona keys
_TASK_TO_PERSONA: dict[str, str] = {
    "debug": "debugger",
    "review": "reviewer",
    "refactor": "refactorer",
    "explain": "explainer",
    "architect": "architect",
    "teacher": "teacher",
    "write_file": "code_writer",
    "edit_file": "code_writer",
    "git": "general",
    "bash": "general",
    "search": "general",
    "general": "general",
}


@dataclass
class PromptContext:
    """Context information for prompt rendering."""
    user_input: str = ""
    file_path: str | None = None
    file_content: str | None = None
    git_status: str | None = None
    language: str | None = None
    error_message: str | None = None
    conversation_history: str | None = None
    working_directory: str | None = None
    user_preferences: dict[str, Any] = field(default_factory=dict)
    custom_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RenderedPrompt:
    """Final rendered prompt ready for LLM."""
    system_prompt: str
    user_prompt: str
    task_type: str
    persona: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AdvancedPromptEngine:
    """Multi-layer prompt engine with persona-driven, context-aware prompts."""

    def __init__(self) -> None:
        self._core_identity = CORE_IDENTITY
        self._personas = dict(PERSONAS)
        self._task_templates = dict(TASK_TEMPLATES)
        self._tool_awareness = TOOL_AWARENESS
        self._output_format = OUTPUT_FORMAT
        self._user_preferences: dict[str, Any] = {}
        self._task_history: list[dict[str, str]] = []

    # ── configuration ─────────────────────────────────────────────

    def set_user_preferences(self, prefs: dict[str, Any]) -> None:
        """Set user preferences that affect prompt generation.
        
        Keys:
        - language: preferred programming language
        - style: "concise" | "detailed" | "verbose"
        - format: "functional" | "oop" | "both"
        - test_first: bool
        - type_hints: bool
        - comments: bool
        """
        self._user_preferences.update(prefs)

    def register_persona(self, name: str, definition: str) -> None:
        """Register a custom persona."""
        self._personas[name] = definition

    def register_task_template(self, name: str, template: str) -> None:
        """Register a custom task template."""
        self._task_templates[name] = template

    # ── core ──────────────────────────────────────────────────────

    def build_prompt(
        self,
        user_input: str,
        context: PromptContext | None = None,
        task_type: str | None = None,
    ) -> RenderedPrompt:
        """Build a complete prompt for the LLM.
        
        This is the main entry point. It assembles all layers:
        1. Core identity
        2. Detected or specified persona
        3. Task-specific instructions
        4. Context (git, files, etc.)
        5. Tool awareness
        6. Output format
        """
        ctx = context or PromptContext(user_input=user_input)
        if not ctx.user_input:
            ctx.user_input = user_input

        # Auto-detect task type if not specified
        detected_type = task_type or self._detect_task_type(ctx.user_input)
        persona = self._select_persona(detected_type)
        task_instruction = self._build_task_instruction(detected_type, ctx)

        # Assemble layers
        system_parts = [
            self._core_identity,
            persona,
            self._tool_awareness,
            self._output_format,
        ]

        # Add user preferences if set
        if self._user_preferences:
            system_parts.append(self._render_preferences())

        # Add context
        context_text = self._build_context_text(ctx)
        if context_text:
            system_parts.append(context_text)

        system_prompt = "\n\n".join(system_parts)
        user_prompt = task_instruction

        # Track task
        self._task_history.append({"type": detected_type, "input": user_input[:200]})

        return RenderedPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task_type=detected_type,
            persona=persona.split("\n")[0].replace("## ", ""),  # e.g. "Current Mode: Debugger"
            metadata={
                "context_keys": list(ctx.custom_context.keys()) if ctx.custom_context else [],
                "has_file_context": ctx.file_content is not None,
                "has_git_context": ctx.git_status is not None,
            },
        )

    def build_conversation_prompt(
        self,
        user_input: str,
        history: list[dict[str, str]],
        context: PromptContext | None = None,
    ) -> RenderedPrompt:
        """Build prompt with conversation history for multi-turn interactions."""
        ctx = context or PromptContext(user_input=user_input)
        ctx.user_input = user_input

        detected_type = self._detect_task_type(user_input)
        persona = self._select_persona(detected_type)

        system_parts = [
            self._core_identity,
            persona,
            self._tool_awareness,
            self._output_format,
        ]

        # Add conversation context
        if history:
            history_summary = self._summarize_history(history)
            system_parts.append(f"## Conversation Context\n{history_summary}\n")

        context_text = self._build_context_text(ctx)
        if context_text:
            system_parts.append(context_text)

        return RenderedPrompt(
            system_prompt="\n\n".join(system_parts),
            user_prompt=user_input,
            task_type=detected_type,
            persona=persona.split("\n")[0].replace("## ", ""),
            metadata={"turns": len(history)},
        )

    def build_self_correction_prompt(
        self,
        original_attempt: str,
        error_feedback: str,
        context: PromptContext | None = None,
    ) -> RenderedPrompt:
        """Build prompt for self-correction loop after an error."""
        ctx = context or PromptContext()

        system_parts = [
            self._core_identity,
            PERSONAS["debugger"],
            self._tool_awareness,
            """\
## Self-Correction Mode
Your previous attempt had an error. Analyze what went wrong and try again.

### What to do:
1. Read the error carefully
2. Understand WHY your previous approach failed
3. Try a DIFFERENT approach — don't just repeat the same thing
4. Be explicit about what you're changing and why
5. Verify the fix works before declaring it done
""",
        ]

        user_prompt = (
            f"## Previous Attempt\n{original_attempt}\n\n"
            f"## Error Feedback\n{error_feedback}\n\n"
            f"## Task\n{ctx.user_input or 'Fix the issue described above.'}"
        )

        return RenderedPrompt(
            system_prompt="\n\n".join(system_parts),
            user_prompt=user_prompt,
            task_type="self_correction",
            persona="Self-Correction Mode",
            metadata={"attempt": 1},
        )

    # ── detection ─────────────────────────────────────────────────

    def _detect_task_type(self, user_input: str) -> str:
        """Auto-detect task type from user input."""
        text = user_input.lower()

        # Priority-based detection: check higher-specificity types first
        priority_order = [
            "debug", "git", "write_file", "edit_file", "review", "refactor",
            "explain", "architect", "teacher", "bash", "search", "general",
        ]

        for task_type in priority_order:
            keywords = _TASK_KEYWORDS.get(task_type, [])
            for kw in keywords:
                if kw in text:
                    return task_type

        return "general"

    def _select_persona(self, task_type: str) -> str:
        """Select the best persona for the task type."""
        persona_key = _TASK_TO_PERSONA.get(task_type, task_type)
        return self._personas.get(persona_key, self._personas["general"])

    def _build_task_instruction(self, task_type: str, ctx: PromptContext) -> str:
        """Build task-specific instruction with context variables."""
        template = self._task_templates.get(task_type, self._task_templates["general_task"])
        return self._render_template(template, self._build_variables(ctx))

    # ── rendering ─────────────────────────────────────────────────

    @staticmethod
    def _render_template(template: str, variables: dict[str, Any]) -> str:
        """Render template with {{var}} and {{#if var}}...{{/if}} support."""
        result = template

        # Handle {{#if var}}...{{/if}} blocks
        pattern = re.compile(r'\{\{#if (\w+)\}\}(.*?)\{\{/if\}\}', re.DOTALL)
        while True:
            match = pattern.search(result)
            if not match:
                break
            var_name = match.group(1)
            block = match.group(2)
            if variables.get(var_name):
                rendered = AdvancedPromptEngine._render_simple_vars(block, variables)
                result = result[:match.start()] + rendered + result[match.end():]
            else:
                result = result[:match.start()] + result[match.end():]

        return AdvancedPromptEngine._render_simple_vars(result, variables)

    @staticmethod
    def _render_simple_vars(text: str, variables: dict[str, Any]) -> str:
        def replacer(match: re.Match) -> str:
            val = variables.get(match.group(1), "")
            return str(val) if val is not None else ""
        return re.sub(r'\{\{(\w+)\}\}', replacer, text)

    def _build_variables(self, ctx: PromptContext) -> dict[str, Any]:
        """Build variable dict for template rendering."""
        return {
            "file_path": ctx.file_path or "",
            "file_content": ctx.file_content or "",
            "language": ctx.language or self._user_preferences.get("language", ""),
            "error": ctx.error_message or "",
            "description": ctx.user_input or "",
            "command": ctx.custom_context.get("command", ""),
            "working_dir": ctx.working_directory or "",
            "action": ctx.custom_context.get("action", ""),
            "message": ctx.custom_context.get("message", ""),
            "branch": ctx.custom_context.get("branch", ""),
            "pattern": ctx.custom_context.get("pattern", ""),
            "glob_pattern": ctx.custom_context.get("glob_pattern", ""),
            "scope": ctx.custom_context.get("scope", ""),
            "context": ctx.custom_context.get("context_text", ""),
            "old_code": ctx.custom_context.get("old_code", ""),
            "new_code": ctx.custom_context.get("new_code", ""),
            "requirements": ctx.custom_context.get("requirements", ""),
            "goal": ctx.custom_context.get("goal", ""),
            "code": ctx.file_content or ctx.custom_context.get("code", ""),
            "focus_areas": ctx.custom_context.get("focus_areas", ""),
        }

    def _build_context_text(self, ctx: PromptContext) -> str:
        """Build context section for the prompt."""
        parts = []

        if ctx.git_status:
            parts.append(f"## Git Status\n```\n{ctx.git_status}\n```\n")

        if ctx.file_path and ctx.file_content:
            parts.append(
                f"## File: {ctx.file_path}\n```\n{ctx.file_content}\n```\n"
            )
        elif ctx.file_path:
            parts.append(f"## File: {ctx.file_path}\n(content not yet loaded)\n")

        if ctx.conversation_history:
            parts.append(f"## Recent Conversation\n{ctx.conversation_history}\n")

        return "\n".join(parts)

    def _render_preferences(self) -> str:
        """Render user preferences as system context."""
        prefs = self._user_preferences
        lines = ["## User Preferences"]
        if prefs.get("style"):
            lines.append(f"- Response style: {prefs['style']}")
        if prefs.get("format"):
            lines.append(f"- Code style: {prefs['format']}")
        if prefs.get("test_first"):
            lines.append("- Test-driven: write tests before implementation")
        if prefs.get("type_hints"):
            lines.append("- Always include type hints")
        if prefs.get("comments"):
            lines.append("- Include detailed comments")
        if prefs.get("language"):
            lines.append(f"- Primary language: {prefs['language']}")
        return "\n".join(lines)

    @staticmethod
    def _summarize_history(history: list[dict[str, str]]) -> str:
        """Summarize conversation history for context."""
        lines = []
        for msg in history[-10:]:  # Last 10 messages
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    # ── introspection ─────────────────────────────────────────────

    def get_task_history(self, limit: int = 50) -> list[dict[str, str]]:
        """Get recent task history."""
        return self._task_history[-limit:]

    def clear_task_history(self) -> None:
        """Clear task history."""
        self._task_history.clear()

    def stats(self) -> dict[str, Any]:
        """Get engine statistics."""
        task_counts: dict[str, int] = {}
        for entry in self._task_history:
            t = entry.get("type", "unknown")
            task_counts[t] = task_counts.get(t, 0) + 1
        return {
            "total_tasks": len(self._task_history),
            "task_distribution": task_counts,
            "registered_personas": list(self._personas.keys()),
            "user_preferences": self._user_preferences,
        }
