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
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

# Cap on concurrent ClaudePlanner subprocess calls when the Decomposer fans
# the mission into multiple sub_intents. Sequential execution was the load-
# bearing bottleneck: ~80s per sub_intent × 9 sub_intents = 720s observed
# 2026-05-20 on the GMX Nexus session, causing every COMPOSE to SIGTERM at
# the Lucid-side 300s timeout. Each ClaudePlanner call shells to an
# independent `claude -p` subprocess against the same OAuth token, so
# concurrency is safe up to whatever Anthropic's per-account quota allows.
# 6 is the comfortable default; override with NEUMANN_MAX_PARALLEL_PLANNERS.
NEUMANN_MAX_PARALLEL_PLANNERS = int(os.environ.get("NEUMANN_MAX_PARALLEL_PLANNERS", "6"))

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
try:
    from ._telemetry import invocation
except Exception:
    from contextlib import contextmanager
    @contextmanager
    def invocation(_h):
        class _N:
            def input(self, **kw): pass
            def output(self, **kw): pass
            def outcome(self, *a, **kw): pass
            def skill(self, *a, **kw): pass
        yield _N()


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
        with invocation('neumann') as _t:
            _t.input(prompt_len=len(prompt), has_interviewer=self.interviewer is not None)

            t0 = time.perf_counter()
            shape_decision = self.shape_classifier.classify(prompt)
            _t.skill('shape_classifier', 'success', int((time.perf_counter() - t0) * 1000))

            # Fast path: SINGLE_TASK shape + no interviewer = v1 direct route.
            if shape_decision.shape == Shape.SINGLE_TASK and self.interviewer is None:
                task = PlannedTask.from_prompt(prompt)
                trace = self._route_one(task, env, shape_decision=shape_decision)
                _t.output(path='fast', shape=str(shape_decision.shape), routes=1)
                return PipelineResult(
                    shape_decision=shape_decision,
                    confirmed_intent=None,
                    plan=None,
                    routes=(trace,),
                )

            # Full path: LLM translation (or synthesize) → Decompose →
            # Plan-per-intent → routes.
            if self.interviewer is not None:
                t0 = time.perf_counter()
                confirmed = self.interviewer.interview(prompt, env=env)
                _t.skill('interviewer', 'success', int((time.perf_counter() - t0) * 1000))
            else:
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

            t0 = time.perf_counter()
            sub_intents = self.decomposer.decompose(confirmed)
            _t.skill('decomposer', 'success', int((time.perf_counter() - t0) * 1000))

            # Run ClaudePlanner concurrently across all sub_intents. Each
            # call is independent (own context, own subprocess, no shared
            # mutable state). ThreadPoolExecutor is the right shape since
            # the work is I/O-bound — every call spends ~80s waiting on
            # the claude subprocess to return. ex.map preserves order, so
            # sub_plans remains aligned with sub_intents indices.
            def _plan_one_sub_intent(sub_intent):
                planner_context = {**env, "confirmed_intent": sub_intent}
                t0 = time.perf_counter()
                sub_plan = self.planner.plan(
                    sub_intent.confirmed_intent, context=planner_context
                )
                return sub_plan, int((time.perf_counter() - t0) * 1000)

            max_workers = max(1, min(len(sub_intents), NEUMANN_MAX_PARALLEL_PLANNERS))
            if max_workers > 1 and len(sub_intents) > 1:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="neumann-planner") as ex:
                    results = list(ex.map(_plan_one_sub_intent, sub_intents))
            else:
                results = [_plan_one_sub_intent(si) for si in sub_intents]

            all_tasks: list[PlannedTask] = []
            sub_plans: list[Plan] = []
            for sub_plan, dur_ms in results:
                _t.skill('planner', 'success', dur_ms)
                sub_plans.append(sub_plan)
                all_tasks.extend(sub_plan.tasks)

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

            t0 = time.perf_counter()
            plan = self.task_decomposer.decompose(plan)
            _t.skill('task_decomposer', 'success', int((time.perf_counter() - t0) * 1000))

            routes = tuple(
                self._route_one(t, env, shape_decision=shape_decision) for t in plan.tasks
            )
            _t.output(
                path='full',
                shape=str(shape_decision.shape),
                sub_intents=len(sub_intents),
                tasks=len(plan.tasks),
                routes=len(routes),
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
