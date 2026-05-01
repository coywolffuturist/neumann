# Spec: Neumann Pipeline Ordering — Decomposer-before-Planner-per-intent

**Status:** Designed 2026-05-01. **APPROVED & SHIPPED 2026-05-01** in commit `74c36c0` on `feature/router-decomposer` (PR #43). All 160 tests pass. This spec stays as historical record of the design rationale.
**Parent PR:** #43 — see PR description for the as-shipped summary.

---

## Goal

Reorder Neumann's `RouterPipeline` so the Decomposer operates on `ConfirmedIntent` (intent-level, prose-shaped) BEFORE the Planner runs, instead of operating on `Plan` (task-level) AFTER. The Planner then runs once per sub-intent and produces 1+ tasks each. This matches Brendan's column-mapping semantics:

```
Prompt
  → LLM translation (Interviewer / clarify)
  → Decomposer (split intent into N sub-intents — operates on prose / ConfirmedIntent)
  → Planner (runs ONCE PER SUB-INTENT → 1+ tasks each)
  → emit tasks into Fusion's Todo column
  ↓
[Fusion takes over]
Todo (router assigns persona) → In Progress (executor) → In Review (QA)
```

## Context

PR #43 added a `Decomposer` that operates on `Plan` (post-Planner, task-level). That's overlap-y with the Planner's own decomposition (the Planner already produces N tasks; the post-Planner Decomposer is a second pass to split oversized ones). Brendan's stated ordering is cleaner: Decomposer operates BEFORE the Planner, on the intent itself.

Single Planner invocation = single focused intent → uniformly tight specs → no FN-009-style sub-agent delegation thrash. Each Planner call becomes a 1-to-1+ spec writer instead of an N-to-1 mission organizer.

This spec exists because on 2026-04-30 I tried to ship the refactor without Brendan's explicit approval. He course-corrected; the design is captured here so the implementation can be approved + executed cleanly when he gives the go-ahead.

References: `feedback_neumann_pipeline_ordering.md`, `feedback_neumann_top_of_funnel.md`, `feedback_micro_tasks_high_probability.md`, `feedback_one_clean_path.md`.

## Acceptance criteria

1. `neumann/router/decomposer.py` exposes `decompose(intent: ConfirmedIntent) -> list[ConfirmedIntent]` as the primary public method. The old `decompose(plan: Plan) -> Plan` is removed (no backward-compat shim — clean replacement).
2. `neumann/router/pipeline.py` `process()` flow:
   - SINGLE_TASK + no-interviewer fast-path preserved (skip everything, route raw prompt as PlannedTask).
   - Otherwise: Interviewer (or synthesize passthrough) → Decomposer → for each sub-intent: Planner → Plan with 1+ tasks → concatenate into single Plan stamped with original ConfirmedIntent → per-task routing.
3. Decomposer threshold logic adapted for prose:
   - Count file paths in `intent.confirmed_intent + success_criteria + constraints` text via regex.
   - Count distinct output verbs in same prose.
   - Estimate lines via char count / 50.
   - `len(success_criteria) > max_distinct_outputs` is also a hard split signal.
4. Decomposer split rules:
   - If `success_criteria` declared and exceeds threshold, split along criteria — each criterion becomes its own sub-intent.
   - If criteria absent but threshold exceeded, infer seams from bullet items / verb-led sentences / file mentions (best-effort).
   - Always emit one final integration sub-intent that depends on every child via `extra["depends_on_sub_intents"]`.
   - Each child carries `extra["parent_intent_id"]` and `extra["sub_intent_id"]`.
5. Tests in `tests/router/test_decomposer.py` rewritten to test intent-level inputs/outputs:
   - Below-threshold passthrough (returns `[intent]`).
   - Above-threshold split (returns N children + 1 integration).
   - Children carry `parent_intent_id` and unique `sub_intent_id`.
   - Integration carries `is_integration: True` and `depends_on_sub_intents` tuple.
   - Loader fault-tolerance (missing/malformed/partial JSON → defaults).
   - Inline `thresholds={...}` argument overrides rules file.
6. Existing pipeline tests (`tests/router/test_pipeline.py`, `tests/router/test_pipeline_with_interview.py`) still pass after the reorder. Adjust expected mission-test path: MockPlanner gets called once per sub-intent now, not once for the whole prompt.
7. Full project test suite: 158+ passing. No regressions.
8. `pipeline.py` docstring documents the new stage order explicitly.
9. `RouterPipeline.__init__` still accepts `decomposer=None` for test injection of a stub.

## Architecture

### Type signatures (after refactor)

```python
class Decomposer:
    def __init__(
        self,
        rules_path: Path | str | None = None,
        thresholds: dict[str, int] | None = None,
    ) -> None: ...

    def decompose(self, intent: ConfirmedIntent) -> list[ConfirmedIntent]:
        """Returns [intent] when under threshold; [child_1, ..., child_N, integration] when over."""
```

### Pipeline.process() new flow

```python
def process(self, prompt: str, env: dict | None = None) -> PipelineResult:
    env = env or {}
    shape = self.shape_classifier.classify(prompt)

    # Fast path: SINGLE_TASK + no interviewer = v1 direct route (preserves backward compat)
    if shape.shape == Shape.SINGLE_TASK and self.interviewer is None:
        task = PlannedTask.from_prompt(prompt)
        trace = self._route_one(task, env, shape_decision=shape)
        return PipelineResult(shape, None, None, (trace,))

    # LLM translation (or synthesize passthrough)
    if self.interviewer is not None:
        confirmed = self.interviewer.interview(prompt, env=env)
    else:
        confirmed = ConfirmedIntent(
            raw_prompt=prompt,
            confirmed_intent=prompt,
            target_repo=env.get("target_repo", ""),
        )
    env = {**env, "target_repo": confirmed.target_repo, "confirmed_intent": confirmed}

    # Decomposer splits intent → N sub-intents
    sub_intents = self.decomposer.decompose(confirmed)

    # Planner runs ONCE PER SUB-INTENT
    all_tasks: list[PlannedTask] = []
    for sub in sub_intents:
        ctx = {**env, "confirmed_intent": sub}
        plan = self.planner.plan(sub.confirmed_intent, context=ctx)
        all_tasks.extend(plan.tasks)

    full_plan = Plan(
        mission_title=confirmed.confirmed_intent[:120] or "Untitled",
        summary=confirmed.confirmed_intent,
        assumptions=(),
        tasks=tuple(all_tasks),
        confirmed_intent=confirmed,
    )

    routes = tuple(self._route_one(t, env, shape_decision=shape) for t in full_plan.tasks)
    return PipelineResult(shape, confirmed, full_plan, routes)
```

### Heuristics adapted for prose

- File detection regex: `\b[\w][\w./-]*\.(?:py|js|ts|tsx|jsx|mjs|cjs|html|css|scss|sql|json|yaml|yml|md|sh|toml)\b` (case-insensitive)
- Output verb regex: `\b(create|add|implement|build|generate|write|extract|introduce|refactor|wire)\b`
- Line estimate: `len(prose) // 50`

### Seam inference (when criteria absent)

In priority order:
1. Bullet items (`-`, `*`, `•` prefixed lines): if ≥2, split along these
2. Verb-led sentences (sentence containing an output verb): if ≥2, split along these
3. Distinct file mentions: if ≥2, each becomes "Work on `<file>`" sub-intent
4. Pathological case (threshold exceeded but no clean seams): return `[intent]` unchanged — do NOT produce junk children

## Implementation steps

1. Replace `Decomposer.decompose` body to operate on `ConfirmedIntent`. Remove old `Plan` signature.
2. Add helpers: `_collect_prose`, `_count_distinct_files` (file regex), `_count_distinct_outputs` (verb regex on prose), `_infer_seams`.
3. Update `_split` to emit `ConfirmedIntent` children + integration instead of `PlannedTask`.
4. Refactor `pipeline.py` `process()` per the architecture above. Preserve fast-path.
5. Rewrite `tests/router/test_decomposer.py` for intent-level cases.
6. Update existing pipeline tests as needed (minimal — most should pass since MockPlanner handles arbitrary input).
7. Run `pytest -q` from repo root. Expect 158+ pass.
8. Update `pipeline.py` module docstring (already done in this branch — verify after changes).
9. Update `__init__.py` exports if signatures changed.

## Anti-patterns

- **Do NOT keep both `decompose(plan)` and `decompose(intent)` signatures as overloads.** One clean interface. Delete the old.
- **Do NOT route through the post-Planner Decomposer "for safety."** That's the wrong ordering; it muddles the architecture.
- **Do NOT allow Decomposer to recurse infinitely.** If a single sub-intent still exceeds threshold after split, log a warning and emit it anyway (don't keep splitting). Guard against pathological prose.
- **Do NOT remove the SINGLE_TASK + no-interviewer fast-path.** Existing callers (test_pipeline.py's `test_single_task_prompt_routes_directly`) depend on it.
- **Do NOT ship without Brendan's explicit "go ahead and build this" approval.** This spec exists precisely because that approval was bypassed once. The lesson is captured in `feedback_one_clean_path.md` and `feedback_neumann_pipeline_ordering.md`.

## Open questions

- Should the Decomposer's threshold limits be configurable per-project (different defaults for lucid vs neumann) or globally? Current: globally via `decomposition_rules.json`. Decision deferred until we see real usage.
- Should integration sub-intents have their own success criteria? Currently set to a generic "All child outputs are integrated cleanly." Refine as we learn.
- Should the rules JSON support priority-ordered alternative seam strategies (e.g., "prefer success_criteria, fall back to bullets, fall back to file mentions")? Current: hardcoded fallback chain in `_infer_seams`. Could lift into rules.

## References

- Memory: `feedback_neumann_pipeline_ordering.md`, `feedback_neumann_top_of_funnel.md`, `feedback_micro_tasks_high_probability.md`, `feedback_one_clean_path.md`, `project_swarm_vision.md`
- PR: #43 (current state, pre-refactor)
- Related specs: `qa-agent.md` (downstream consumer of the Plan emitted by this pipeline)
- Cross-repo: `coywolffuturist/lucid` (consumer of Neumann's pipeline via the intake panel — see `lucid/docs/specs/intake-panel.md`)
