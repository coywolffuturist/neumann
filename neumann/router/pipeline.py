"""RouterPipeline — orchestrates shape → plan → classify → context → select → validate → fallback.

This is the public entry point. ``RouterPipeline`` is composed of the
individual modules so callers can swap any of them for a custom impl
(custom rules path, alternative resolver, real LLM tiebreak callback,
etc.).

Mirrors ``neumann.pipeline.NeumannPipeline``.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from .context_resolver import ContextResolver
from .fallback import RoutingFallback
from .interviewer import Interviewer
from .persona_selector import PersonaSelector
from .planner_protocol import MockPlanner, Planner
from .registry import PersonaRegistry
from .shape_classifier import ShapeClassifier
from .task_classifier import TaskTypeClassifier
from .types import (
    ConfirmedIntent,
    PersonaDecision,
    Plan,
    PlannedTask,
    RoutingContext,
    RoutingTrace,
    Shape,
    ShapeDecision,
    ValidationResult,
)
from .validator import RoutingValidator


@dataclass
class PipelineResult:
    shape_decision: ShapeDecision
    confirmed_intent: ConfirmedIntent | None  # populated when interview is enabled
    plan: Plan | None  # populated only for mission-shape prompts after interview
    routes: tuple[RoutingTrace, ...]


class RouterPipeline:
    def __init__(
        self,
        shape_classifier: ShapeClassifier | None = None,
        task_classifier: TaskTypeClassifier | None = None,
        context_resolver: ContextResolver | None = None,
        persona_selector: PersonaSelector | None = None,
        validator: RoutingValidator | None = None,
        fallback: RoutingFallback | None = None,
        registry: PersonaRegistry | None = None,
        planner: Planner | None = None,
        interviewer: Interviewer | None = None,
        allowed_orgs: tuple[str, ...] = (),
    ) -> None:
        self.shape_classifier = shape_classifier or ShapeClassifier()
        self.task_classifier = task_classifier or TaskTypeClassifier()
        self.context_resolver = context_resolver or ContextResolver()
        self.persona_selector = persona_selector or PersonaSelector()
        self.registry = registry or PersonaRegistry()
        self.validator = validator or RoutingValidator(registry=self.registry)
        self.fallback = fallback or RoutingFallback(registry=self.registry)
        self.planner = planner or MockPlanner()
        # Interviewer is opt-in. Callers wire one in for entry points where
        # the human is reachable (Slack thread, web chat, terminal stdin).
        # When None, the pipeline behaves like its v1 self: no interview, raw
        # prompt goes straight to planner / direct route.
        self.interviewer = interviewer
        self.allowed_orgs = allowed_orgs

    # ── public surface ────────────────────────────────────────

    def process(self, prompt: str, env: dict[str, Any] | None = None) -> PipelineResult:
        """Run a raw user prompt through the full pipeline.

        Order of stages: ShapeClassifier → (optional Interviewer) →
        (Planner if mission, else single PlannedTask) → per-task route.

        The Interviewer is opt-in. When wired, every prompt — single-task
        or mission — passes through it. The Interviewer's ``ConfirmedIntent``
        becomes the input to the Planner. Without an Interviewer the
        pipeline behaves like its v1 self.
        """
        env = env or {}
        shape_decision = self.shape_classifier.classify(prompt)

        # Stage: Interview (optional but recommended for any human-facing entry point)
        confirmed: ConfirmedIntent | None = None
        if self.interviewer is not None:
            confirmed = self.interviewer.interview(prompt, env=env)
            # Merge target_repo into env so the rest of the pipeline (and
            # downstream `fn task create` shell-out) sees the human-confirmed repo.
            env = {**env, "target_repo": confirmed.target_repo, "confirmed_intent": confirmed}

        if shape_decision.shape == Shape.SINGLE_TASK:
            base_prompt = confirmed.confirmed_intent if confirmed else prompt
            task = PlannedTask.from_prompt(base_prompt)
            trace = self._route_one(task, env, shape_decision=shape_decision)
            return PipelineResult(
                shape_decision=shape_decision,
                confirmed_intent=confirmed,
                plan=None,
                routes=(trace,),
            )

        # Mission shape — invoke planner. Pass the confirmed intent as
        # additional context so the planner stays grounded.
        planner_input = confirmed.confirmed_intent if confirmed else prompt
        planner_context = {**env}
        if confirmed is not None:
            planner_context["confirmed_intent"] = confirmed
        plan = self.planner.plan(planner_input, context=planner_context)
        if confirmed is not None:
            # Stamp the link from plan back to the interview that authorized it.
            plan = Plan(
                mission_title=plan.mission_title,
                summary=plan.summary,
                assumptions=plan.assumptions,
                tasks=plan.tasks,
                confirmed_intent=confirmed,
            )

        routes = tuple(
            self._route_one(t, env, shape_decision=shape_decision) for t in plan.tasks
        )
        return PipelineResult(
            shape_decision=shape_decision,
            confirmed_intent=confirmed,
            plan=plan,
            routes=routes,
        )

    def route(self, task: PlannedTask, env: dict[str, Any] | None = None) -> RoutingTrace:
        """Route a single PlannedTask, skipping shape classification + planning."""
        return self._route_one(task, env or {}, shape_decision=None)

    def classify_shape(self, prompt: str) -> ShapeDecision:
        return self.shape_classifier.classify(prompt)

    def plan(self, prompt: str, env: dict[str, Any] | None = None) -> Plan:
        return self.planner.plan(prompt, context=env)

    # ── internals ─────────────────────────────────────────────

    def _route_one(
        self,
        task: PlannedTask,
        env: dict[str, Any],
        shape_decision: ShapeDecision | None,
    ) -> RoutingTrace:
        start = time.perf_counter()

        task_type, type_meta = self.task_classifier.classify(task)
        context = self.context_resolver.resolve(task, env)
        decision = self.persona_selector.select(
            task_type=task_type,
            context=context,
            task=task,
            type_match_metadata=type_meta,
        )
        decision = self.fallback.resolve(decision, task=task, context=context)
        validation = self.validator.validate(decision, context=context)

        decision = PersonaDecision(
            persona=decision.persona,
            task_type=decision.task_type,
            matched_rule_priority=decision.matched_rule_priority,
            dispatch_priority=decision.dispatch_priority,
            fallback_used=decision.fallback_used,
            trace=decision.trace + (
                f"validation: {validation.severity}"
                + (f" — {validation.reason}" if validation.reason else ""),
            ),
        )

        duration_ms = (time.perf_counter() - start) * 1000.0
        input_hash = "sha256:" + hashlib.sha256(task.title.encode("utf-8")).hexdigest()[:12]

        return RoutingTrace(
            input_hash=input_hash,
            shape_decision=shape_decision,
            task=task,
            context=context,
            persona_decision=decision,
            duration_ms=duration_ms,
        )
