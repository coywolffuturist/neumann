"""Tiebreak callbacks for ``RoutingFallback``.

When ``PersonaSelector`` can't pick a persona deterministically (no rule
matches, or the matched persona is unavailable), ``RoutingFallback``
either uses the generic engineer default OR delegates to a tiebreak
callback. This module provides factory functions for callbacks backed
by an LLM — currently the Claude CLI.

These are **pluggable**, not default-on. A typical wiring::

    from neumann.router import RouterPipeline, RoutingFallback
    from neumann.router.tiebreaks import make_claude_cli_tiebreak

    pipeline = RouterPipeline(
        fallback=RoutingFallback(tiebreak_callback=make_claude_cli_tiebreak()),
    )

Why a small LLM at the boundary: Neumann's architecture is "LLM generates,
Neumann routes". Routing should be deterministic. But for the genuinely
ambiguous prompts that no rule matches, a single cheap LLM call (haiku-class)
is preferable to defaulting to a generic engineer who might be wrong. The
fallback path is logged separately so we can mine it later for new rules.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Iterable

from .fallback import TiebreakCallback
from .types import FALLBACK_SENTINEL, PersonaId, PlannedTask, RoutingContext


DEFAULT_CLAUDE_BIN = "/opt/homebrew/bin/claude"
DEFAULT_TIMEOUT_SECONDS = 60


def _build_prompt(task: PlannedTask, context: RoutingContext, candidates: list[dict]) -> str:
    """Build the haiku-class system prompt. Short, explicit output schema."""
    lines: list[str] = [
        "You are routing a planned task to the right specialist persona.",
        "",
        "Task:",
        f"  title: {task.title}",
    ]
    if task.description:
        lines.append(f"  description: {task.description}")
    if task.type_hints:
        lines.append(f"  type_hints: {', '.join(task.type_hints)}")
    if task.target_files:
        lines.append(f"  target_files: {', '.join(task.target_files)}")
    if task.acceptance_criteria:
        lines.append(f"  acceptance_criteria: {task.acceptance_criteria}")

    lines.append("")
    lines.append(f"Project context: project_type={context.project_type}")
    if context.persona_load:
        lines.append(f"Current load per persona: {dict(context.persona_load)}")

    lines.append("")
    lines.append("Available personas (pick one by id):")
    for c in candidates:
        pid = c.get("id", "?")
        name = c.get("name", pid)
        desc = c.get("description", "")
        lines.append(f"  - {pid}: {name} — {desc}")

    lines.append("")
    lines.append("Output a single JSON object with one field: {\"persona_id\": \"<id>\"}.")
    lines.append("No prose, no markdown fence, no preamble.")
    return "\n".join(lines)


def _parse_persona_id(stdout: str, valid_ids: Iterable[str]) -> PersonaId | None:
    """Strip markdown fences if present, parse JSON, validate id."""
    valid = set(valid_ids)
    text = stdout.strip()
    # Permissive: strip a leading ```json ... ``` fence if the model wrapped it.
    if text.startswith("```"):
        first_nl = text.find("\n")
        last_fence = text.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            text = text[first_nl + 1 : last_fence].strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    pid = obj.get("persona_id")
    if isinstance(pid, str) and pid in valid:
        return pid
    return None


def make_claude_cli_tiebreak(
    claude_bin: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    *,
    fallback_persona: PersonaId = "engineer",
) -> TiebreakCallback:
    """Factory for a tiebreak callback that shells out to ``claude --print``.

    Returns a callable matching ``RoutingFallback``'s ``TiebreakCallback``
    signature: ``(task, context, candidates) -> persona_id``.

    The factory itself does no I/O — it builds and returns the callback.
    The callback is the side-effecting one (it spawns a subprocess).

    On any failure (binary missing, timeout, parse error, invalid persona id)
    the callback returns ``fallback_persona`` so routing is never broken
    by an LLM hiccup.
    """
    bin_path = claude_bin or os.environ.get("CLAUDE_BIN") or DEFAULT_CLAUDE_BIN

    def callback(task: PlannedTask, context: RoutingContext, candidates: list[dict]) -> PersonaId:
        # Defensive: skip the sentinel from candidates (FallbackHandler injects it).
        real_candidates = [c for c in candidates if c.get("id") != FALLBACK_SENTINEL]
        if not real_candidates:
            return fallback_persona

        prompt = _build_prompt(task, context, real_candidates)

        if not shutil.which(bin_path) and not os.path.isfile(bin_path):
            # Can't run claude; fall back deterministically.
            return fallback_persona

        try:
            result = subprocess.run(
                [
                    bin_path,
                    "--print",
                    "--permission-mode", "acceptEdits",
                    "--no-session-persistence",
                    "--max-budget-usd", "0.10",
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except (subprocess.TimeoutExpired, OSError):
            return fallback_persona

        if result.returncode != 0:
            return fallback_persona

        chosen = _parse_persona_id(result.stdout, [c["id"] for c in real_candidates])
        return chosen or fallback_persona

    return callback


__all__ = ["make_claude_cli_tiebreak"]
