"""RouterPipeline — orchestrates the universal intake pipeline.

Brendan's column-mapping ordering:

    Prompt
      → LLM translation (Interviewer / clarify)
      → Decomposer (split intent into N sub-intents — operates on prose)
      → Planner (one Planner invocation per sub-intent → 1+ tasks each)
      → emit N tasks into Fusion's Todo column

After this pipeline, Fusion takes over: Todo (router assigns persona) →
In Progress (executor) → In Review (QA agent).

The Decomposer sits BEFORE the Planner so each Planner invocation only
sees one focused sub-intent. This keeps the Planner's input small and
its output uniformly tight (1 intent in → 1+ tasks out), avoiding the
FN-009 trap (sub-agent delegation, infinite worktree thrash).

``RouterPipeline`` is composed of the individual modules so callers can
swap any of them for a custom impl (custom rules path, alternative
resolver, real LLM tiebreak callback, etc.).

See ``docs/specs/pipeline-ordering.md`` for the full design.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from .context_resolver import ContextResolver
from .decomposer import Decomposer
from .fallback import RoutingFallback
from .interviewer import Interviewer
from .task_decomposer import TaskDecomposer
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
        decomposer: Decomposer | None = None,
        task_decomposer: TaskDecomposer | None = None,
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
        # Decomposer enforces the micro-task principle: oversized tasks get
        # split into children + an integration task before routing. Pass
        # ``decomposer=Decomposer(thresholds={...})`` for custom thresholds,
        # or pass a stub for tests that want decomposition disabled.
        self.decomposer = decomposer or Decomposer()
        # TaskDecomposer (FN-002): post-Planner stage that splits oversized
        # PlannedTasks into children + an integration task. Same micro-task
        # principle as the intent-level Decomposer above, applied one stage
        # later so it catches the cases the Planner emits as a single bloated
        # task even after intent-level decomposition.
        self.task_decomposer = task_decomposer or TaskDecomposer()
        # Interviewer is opt-in. Callers wire one in for entry points where
        # the human is reachable (Slack thread, web chat, terminal stdin).
        # When None, the pipeline behaves like its v1 self: no interview, raw
        # prompt goes straight to planner / direct route.
        self.interviewer = interviewer
        self.allowed_orgs = allowed_orgs

    # ── public surface ────────────────────────────────────────

    def process(self, prompt: str, env: dict[str, Any] | None = None) -> PipelineResult:
        """Run a raw user prompt through the full intake pipeline.

        Stage order:
          1. ShapeClassifier — advisory single-vs-mission classification.
          2. Interviewer — LLM translation. Optional. Produces ConfirmedIntent.
          3. Decomposer — split the intent into N sub-intents (passthrough
             for small intents).
          4. Planner — runs ONCE PER SUB-INTENT, producing 1+ tasks each.
          5. Per-task routing — task_classifier → context → persona → fallback → validate.

        The Interviewer is opt-in. When wired, every prompt passes through
        it. When None and Shape says MISSION, the pipeline synthesizes a
        passthrough ConfirmedIntent from the raw prompt so downstream stages
        see uniform input.

        Fast path: when ``Shape.SINGLE_TASK`` AND no interviewer is wired,
        we skip Interview/Decompose/Plan and route the prompt directly as
        a thin PlannedTask. This is the v1 behavior preserved for callers
        that don't need the heavier pipeline.
        """
        env = env or {}
        shape_decision = self.shape_classifier.classify(prompt)

        # Fast path: SINGLE_TASK shape + no interviewer = v1 direct route.
        # Preserves behavior for callers that intentionally skip interview.
        if shape_decision.shape == Shape.SINGLE_TASK and self.interviewer is None:
            task = PlannedTask.from_prompt(prompt)
            trace = self._route_one(task, env, shape_decision=shape_decision)
            return PipelineResult(
                shape_decision=shape_decision,
                confirmed_intent=None,
                plan=None,
                routes=(trace,),
            )

        # Full path: LLM translation (or synthesize) → Decompose →
        # Plan-per-intent → routes.
        if self.interviewer is not None:
            confirmed = self.interviewer.interview(prompt, env=env)
        else:
            # No interviewer wired but mission-shaped prompt — synthesize a
            # passthrough ConfirmedIntent so the rest of the pipeline has
            # uniform input.
            confirmed = ConfirmedIntent(
                raw_prompt=prompt,
                confirmed_intent=prompt,
                target_repo=env.get("target_repo", ""),
            )
        env = {
            **env,
            "target_repo": confirmed.target_repo,
            "confirmed_intent": confirmed,
        }

        # Stage: Decompose intent into sub-intents.
        sub_intents = self.decomposer.decompose(confirmed)

        # Stage: Planner runs once per sub-intent. Each invocation sees a
        # single focused area — the Planner's output stays uniformly tight.
        all_tasks: list[PlannedTask] = []
        sub_plans: list[Plan] = []
        for sub_intent in sub_intents:
            planner_context = {**env, "confirmed_intent": sub_intent}
            sub_plan = self.planner.plan(
                sub_intent.confirmed_intent, context=planner_context
            )
            sub_plans.append(sub_plan)
            all_tasks.extend(sub_plan.tasks)

        # Aggregate per-sub-intent plans into one Plan stamped with the
        # original (pre-decomposition) ConfirmedIntent.
        # mission_title: when no decomposition happened (1 sub-intent), use
        # the Planner's title — it's authoritative for that single scope.
        # When decomposition produced multiple sub-intents, the Planner-side
        # titles are necessarily narrower than the overall mission, so we
        # fall back to the user-confirmed intent as the umbrella title.
        if len(sub_plans) == 1 and sub_plans[0].mission_title:
            mission_title = sub_plans[0].mission_title
        else:
            mission_title = confirmed.confirmed_intent[:120] or "Untitled"

        plan = Plan(
            mission_title=mission_title,
            summary=confirmed.confirmed_intent,
            assumptions=(),
            tasks=tuple(all_tasks),
            confirmed_intent=confirmed,
        )

        # FN-002: post-Planner task-level decomposition. Splits any
        # PlannedTask that exceeds size thresholds into N child tasks + an
        # integration task. Pass-through for tasks within thresholds.
        plan = self.task_decomposer.decompose(plan)

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
