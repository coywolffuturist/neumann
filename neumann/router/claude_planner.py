"""ClaudePlanner — LLM-backed Neumann Planner implementation.

Conforms to the Planner protocol (neumann.router.planner_protocol.Planner):
returns a Plan with mission_title, summary, assumptions, tasks[] when
given a mission prompt. Calls Claude via the with-claude-token wrapper
per the Claude-Max-only rule (no API keys; OAuth token only).

Loads the canonical Planner persona spec from personas/planner.json —
soul + instructionsText become the system prompt. The model emits a
single JSON object matching the schema defined in the soul; we parse
into PlannedTask dataclasses.

Used as the default Planner in Neumann CLI when CLAUDE_CODE_OAUTH_TOKEN
is set. MockPlanner remains for tests + offline development.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from .planner_protocol import PLANNER_SPEC_PATH, load_planner_spec
from .types import Plan, PlannedTask

CLAUDE_BIN = os.environ.get("NEUMANN_CLAUDE_BIN", "/opt/homebrew/bin/claude")
CLAUDE_WRAPPER = os.environ.get(
    "NEUMANN_CLAUDE_WRAPPER",
    "/Users/coywolfden/.coywolf/scripts/with-claude-token.sh",
)
DEFAULT_MODEL = os.environ.get("NEUMANN_PLANNER_MODEL", "claude-opus-4-7")
TIMEOUT_S = int(os.environ.get("NEUMANN_PLANNER_TIMEOUT_S", "180"))


class ClaudePlanner:
    """LLM-backed Planner via the Claude CLI + OAuth wrapper.

    The persona spec at personas/planner.json defines the soul (output
    schema, decomposition principles, no-persona-assignment rule). We
    prepend it as the system prompt for every call.
    """

    def __init__(self, model: str = DEFAULT_MODEL, debug: bool = False) -> None:
        self.model = model
        self.debug = debug
        self._spec = load_planner_spec()

    def _system_prompt(self) -> str:
        soul = self._spec.get("soul", "")
        instr = self._spec.get("instructionsText", "")
        return f"{soul}\n\n## Behavior\n\n{instr}".strip()

    def _user_prompt(self, prompt: str, context: dict[str, Any] | None) -> str:
        parts = [
            "Mission prompt:",
            prompt.strip(),
        ]
        if context:
            parts.extend([
                "",
                "Context (use to inform task shape; do not echo back):",
                json.dumps(context, indent=2, default=str),
            ])
        parts.extend([
            "",
            "Emit exactly the JSON object from your schema. No prose, no code fences.",
        ])
        return "\n".join(parts)

    def _invoke_claude(self, system_prompt: str, user_prompt: str) -> str:
        """Shell out to claude CLI via the OAuth wrapper. Returns raw stdout.

        --tools '' disables the entire built-in toolset for this call.
        The Planner persona is a pure JSON producer (per personas/planner.json
        Output Schema: "I emit exactly that JSON object — nothing before,
        nothing after"); enabling Read/WebFetch/Agent invites tool-call
        loops that stall the subprocess with zero stdout on long missions.
        Observed 2026-05-20: a 12k-char Plan Mode transcript hung
        ClaudePlanner past the 300s parent timeout with empty stdout/stderr.

        --output-format text + --no-session-persistence keep output
        deterministic and side-effect-free.
        --exclude-dynamic-system-prompt-sections strips claude's own
        system additions so only the Planner persona drives behavior.
        """
        cmd = [
            CLAUDE_WRAPPER,
            CLAUDE_BIN,
            "-p",
            "--model", self.model,
            "--tools", "",
            "--output-format", "text",
            "--no-session-persistence",
            "--exclude-dynamic-system-prompt-sections",
            "--append-system-prompt", system_prompt,
        ]
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude exited {result.returncode}: {result.stderr[:500]}"
            )
        return result.stdout

    def _parse_json(self, raw: str) -> dict[str, Any]:
        """Strip code fences / prose preamble; parse the first JSON object."""
        s = raw.strip()
        # Remove markdown fences if the model added them despite instructions.
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
        s = s.strip()
        if not s.startswith("{"):
            i = s.find("{")
            j = s.rfind("}")
            if i >= 0 and j > i:
                s = s[i : j + 1]
        return json.loads(s)

    def _to_plan(self, parsed: dict[str, Any], prompt: str) -> Plan:
        raw_tasks = parsed.get("tasks") or []
        tasks: list[PlannedTask] = []
        for t in raw_tasks:
            if not isinstance(t, dict):
                continue
            tasks.append(
                PlannedTask(
                    title=str(t.get("title") or "").strip()[:240]
                    or "Untitled task",
                    description=str(t.get("description") or ""),
                    type_hints=tuple(t.get("type_hints") or ()),
                    target_files=tuple(t.get("target_files") or ()),
                    acceptance_criteria=str(t.get("acceptance_criteria") or ""),
                    depends_on=tuple(t.get("depends_on") or ()),
                    extra={
                        k: v
                        for k, v in t.items()
                        if k
                        not in (
                            "title",
                            "description",
                            "type_hints",
                            "target_files",
                            "acceptance_criteria",
                            "depends_on",
                        )
                    },
                )
            )
        return Plan(
            mission_title=str(parsed.get("mission_title") or prompt[:80] or "Mission"),
            summary=str(parsed.get("summary") or ""),
            assumptions=tuple(parsed.get("assumptions") or ()),
            tasks=tuple(tasks),
        )

    def plan(self, prompt: str, context: dict[str, Any] | None = None) -> Plan:
        system_prompt = self._system_prompt()
        user_prompt = self._user_prompt(prompt, context)
        raw = self._invoke_claude(system_prompt, user_prompt)
        if self.debug:
            print(f"[ClaudePlanner] raw response ({len(raw)} chars):", raw[:300])
        try:
            parsed = self._parse_json(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"ClaudePlanner could not parse JSON: {e.msg}; raw starts with: {raw[:300]}"
            ) from e
        return self._to_plan(parsed, prompt)
