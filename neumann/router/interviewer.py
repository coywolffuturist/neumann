"""Interviewer — clarifying Q&A loop that produces a ConfirmedIntent.

The Interviewer sits between the user's raw prompt and the LLM Planner.
Its job: make sure the agent *understands* the human's intent before any
plan is written. The output is a structured ``ConfirmedIntent`` that the
human has explicitly approved.

This module ships:

- ``Interviewer`` — the protocol any concrete interviewer implements.
- ``MockInterviewer`` — for tests + offline runs. Returns canned ConfirmedIntent.
- ``CLIInterviewer`` — runs the interview interactively via stdin/stdout.
- ``validate_intent`` — pure function that checks a ConfirmedIntent against
  required-field rules. This is the deterministic gate; the LLM-driven Q&A
  loop is allowed to terminate ONLY when ``validate_intent`` returns valid.

Real LLM-backed interviewers (Slack, web chat) implement ``Interviewer``
and use ``validate_intent`` as the loop terminator.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from .types import ConfirmedIntent, InterviewExchange, ValidationResult

DEFAULT_QUESTIONS_PATH = Path(__file__).parent / "rules" / "interview_questions.json"

# Repo-spec validation: owner/name with no path separators, no domain prefix,
# no .git suffix. The router's PersonaSelector uses the project's own org
# allowlist to validate further; this is just shape-level.
REPO_SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class InterviewQuestion:
    priority: int
    trigger: str
    question: str
    expects: str
    required_field: str | None
    note: str = ""


def load_questions(path: Path | str | None = None) -> list[InterviewQuestion]:
    p = Path(path) if path else DEFAULT_QUESTIONS_PATH
    with open(p) as f:
        raw = json.load(f)
    return sorted(
        [
            InterviewQuestion(
                priority=int(r["priority"]),
                trigger=r["trigger"],
                question=r["question"],
                expects=r["expects"],
                required_field=r.get("required_field"),
                note=r.get("note", ""),
            )
            for r in raw
        ],
        key=lambda q: q.priority,
    )


# ── Validation (deterministic gate) ─────────────────────────────


def validate_intent(intent: ConfirmedIntent, *, allowed_orgs: tuple[str, ...] = ()) -> ValidationResult:
    """Pure function. Schema-validates a ConfirmedIntent.

    Returns ``valid=True`` only when:
    - confirmed_intent is non-empty
    - target_repo is a well-formed owner/name spec
    - target_repo's owner is in allowed_orgs (when supplied)
    - at least one success_criterion is provided
    - human_approved is True

    The Interviewer keeps looping until this returns valid.
    """
    if not intent.confirmed_intent.strip():
        return ValidationResult(valid=False, reason="confirmed_intent is empty", severity="error")
    if not intent.target_repo:
        return ValidationResult(valid=False, reason="target_repo is missing", severity="error")
    if not REPO_SPEC_RE.match(intent.target_repo):
        return ValidationResult(
            valid=False,
            reason=f"target_repo '{intent.target_repo}' is not a valid owner/name spec",
            severity="error",
        )
    if allowed_orgs:
        owner = intent.target_repo.split("/", 1)[0].lower()
        if owner not in {o.lower() for o in allowed_orgs}:
            return ValidationResult(
                valid=False,
                reason=f"target_repo owner '{owner}' not in allowed orgs {list(allowed_orgs)}",
                severity="error",
            )
    if not intent.success_criteria:
        return ValidationResult(
            valid=False,
            reason="at least one success_criterion is required",
            severity="error",
        )
    if not intent.human_approved:
        return ValidationResult(
            valid=False,
            reason="human has not yet approved the agent's understanding",
            severity="warn",
        )
    return ValidationResult(valid=True, severity="ok")


# ── Protocol ────────────────────────────────────────────────────


@runtime_checkable
class Interviewer(Protocol):
    """Anything that runs an interview loop and returns a ConfirmedIntent."""

    def interview(
        self,
        raw_prompt: str,
        *,
        seed: ConfirmedIntent | None = None,
        env: dict[str, Any] | None = None,
    ) -> ConfirmedIntent: ...


# ── MockInterviewer (tests + offline) ───────────────────────────


class MockInterviewer:
    """Returns a canned ConfirmedIntent for tests + offline development.

    Two registration shapes:

    1. ``register(prompt_substring, intent)`` — first matching substring wins.
    2. ``set_default(intent)`` — used when no substring matches.
    """

    def __init__(self) -> None:
        self._fixtures: list[tuple[str, ConfirmedIntent]] = []
        self._default: ConfirmedIntent | None = None

    def register(self, prompt_substring: str, intent: ConfirmedIntent) -> "MockInterviewer":
        self._fixtures.append((prompt_substring.lower(), intent))
        return self

    def set_default(self, intent: ConfirmedIntent) -> "MockInterviewer":
        self._default = intent
        return self

    def interview(
        self,
        raw_prompt: str,
        *,
        seed: ConfirmedIntent | None = None,
        env: dict[str, Any] | None = None,
    ) -> ConfirmedIntent:
        lower = raw_prompt.lower()
        for sub, intent in self._fixtures:
            if sub in lower:
                # Stamp raw_prompt for trace consistency.
                return replace(intent, raw_prompt=raw_prompt)
        if self._default is not None:
            return replace(self._default, raw_prompt=raw_prompt)
        raise RuntimeError(
            "MockInterviewer: no fixture matched and no default set. "
            "Register a fixture or set_default() before calling interview()."
        )


# ── CLIInterviewer (interactive terminal) ───────────────────────


class CLIInterviewer:
    """Runs an interview via stdin/stdout. For local use.

    Real Slack/web interviewers should follow the same shape but route
    questions through their own message channel and read responses from
    incoming messages or webhooks.
    """

    def __init__(
        self,
        allowed_orgs: tuple[str, ...] = (),
        questions_path: Path | str | None = None,
        prompt_io: Callable[[str], str] = input,
        write_io: Callable[[str], None] = print,
    ) -> None:
        self._allowed_orgs = allowed_orgs
        self._questions = load_questions(questions_path)
        self._read = prompt_io
        self._write = write_io

    def interview(
        self,
        raw_prompt: str,
        *,
        seed: ConfirmedIntent | None = None,
        env: dict[str, Any] | None = None,
    ) -> ConfirmedIntent:
        env = env or {}
        intent = seed or ConfirmedIntent(raw_prompt=raw_prompt, confirmed_intent=raw_prompt, target_repo="")
        transcript: list[InterviewExchange] = list(intent.transcript)

        def ask(q: InterviewQuestion, formatted: str) -> str:
            self._write(f"\n[router-interview] {formatted}")
            response = self._read("> ").strip()
            transcript.append(
                InterviewExchange(
                    question=formatted,
                    response=response,
                    asked_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            return response

        # The validator drives the loop. On each iteration: ask one question
        # that addresses whatever validate_intent flagged as failing. If
        # validate_intent passes, exit. If we hit max_rounds without
        # convergence, raise InterviewIncomplete.
        max_rounds = 8
        for _ in range(max_rounds):
            result = validate_intent(intent, allowed_orgs=self._allowed_orgs)
            if result.valid:
                break

            field = self._failure_to_field(result.reason)
            q = self._question_for_field(field) if field else None
            if q is None:
                # No question can address this failure — bail out.
                break

            formatted = self._format_question(q, intent, env)
            response = ask(q, formatted)
            intent = self._apply_response(intent, q, response, env)

        intent = replace(intent, transcript=tuple(transcript))
        final = validate_intent(intent, allowed_orgs=self._allowed_orgs)
        if not final.valid:
            raise InterviewIncomplete(final.reason, partial=intent)
        return intent

    # ── helpers ────────────────────────────────────────────────

    @staticmethod
    def _failure_to_field(reason: str) -> str | None:
        """Map a validate_intent failure reason to the field that needs fixing.

        Pure mapping. The validator's reason strings are stable; if we
        rewrite them, this map needs updating in lockstep.
        """
        lower = reason.lower()
        if "confirmed_intent" in lower:
            return "confirmed_intent"
        if "target_repo" in lower:
            return "target_repo"
        if "success_criterion" in lower or "success_criteria" in lower:
            return "success_criteria"
        if "human" in lower or "approved" in lower:
            return "human_approved"
        return None

    def _question_for_field(self, field_name: str) -> InterviewQuestion | None:
        for q in self._questions:
            if q.required_field == field_name:
                return q
        return None

    @staticmethod
    def _format_question(q: InterviewQuestion, intent: ConfirmedIntent, env: dict[str, Any]) -> str:
        try:
            return q.question.format(
                available_repos=", ".join(env.get("available_repos") or []) or "(none configured)",
                confirmed_intent=intent.confirmed_intent or "(unset)",
                target_repo=intent.target_repo or "(unset)",
                success_criteria="; ".join(intent.success_criteria) or "(unset)",
            )
        except (KeyError, IndexError):
            return q.question

    @staticmethod
    def _apply_response(
        intent: ConfirmedIntent,
        q: InterviewQuestion,
        response: str,
        env: dict[str, Any],
    ) -> ConfirmedIntent:
        if q.required_field == "target_repo":
            return replace(intent, target_repo=response.strip())
        if q.required_field == "success_criteria":
            criteria = tuple(s.strip() for s in re.split(r"[;\n]+", response) if s.strip())
            return replace(intent, success_criteria=criteria or intent.success_criteria)
        if q.required_field == "out_of_scope":
            items = tuple(s.strip() for s in re.split(r"[;\n]+", response) if s.strip())
            return replace(intent, out_of_scope=items)
        if q.required_field == "constraints":
            items = tuple(s.strip() for s in re.split(r"[;\n]+", response) if s.strip())
            return replace(intent, constraints=items)
        if q.required_field == "human_approved":
            approved = response.lower().startswith(("y", "yes", "approve", "ship"))
            if not approved and response.lower().startswith("refine"):
                # User wants to refine — extract the refinement and update confirmed_intent.
                refinement = response.split(":", 1)[1].strip() if ":" in response else ""
                if refinement:
                    return replace(intent, confirmed_intent=refinement, human_approved=False)
                return replace(intent, human_approved=False)
            return replace(intent, human_approved=approved)
        return intent


class InterviewIncomplete(RuntimeError):
    """Raised by an Interviewer when it cannot produce a valid ConfirmedIntent."""

    def __init__(self, reason: str, *, partial: ConfirmedIntent | None = None) -> None:
        super().__init__(reason)
        self.partial = partial
