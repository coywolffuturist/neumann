# neumann.router

> Deterministic persona routing for AI agent systems. Three generative phases (interview → plan → route) with a deterministic schema gate at each handoff.

## What it is

A subpackage of [Neumann](../../README.md) that takes a raw user prompt, makes sure the agent *understands* the human's intent, decomposes the work into specialist tasks, and routes each task to the right persona via dispatch tables.

The three phases:
- **Interview (LLM, optional).** A clarifying Q&A loop that produces a structured `ConfirmedIntent` — the human-approved restatement of intent + target repo + success criteria. The deterministic gate here is `validate_intent`: the loop terminates only when required fields are populated and the human has explicitly approved.
- **Plan (LLM).** A planner takes the confirmed intent and produces a structured `Plan` with one or more `PlannedTask`s. The Planner persona's soul + instructions live in `personas/planner.json`.
- **Route (deterministic).** Each `PlannedTask` flows through classify → resolve context → select persona → validate → fallback. No LLM call required.

## Why this shape

Routing on raw prompts forces routing decisions on under-specified prose — the same problem Neumann was built to solve. The Interview stage stops the agent from speculating; the human explicitly blesses the agent's interpretation before any plan is written. Then routing on **structured planned tasks** lets the dispatch rules lean on `target_files`, `type_hints`, and `acceptance_criteria` — far stronger signals than NL keyword matches.

The pattern: **three generative phases, three structured artifacts, deterministic gates between each.**

## Pipeline

```
User prompt
    ↓
ShapeClassifier            single-task | mission
    ↓
Interviewer (optional)     LLM Q&A loop → ConfirmedIntent (gate: validate_intent)
    ↓
[mission] → Planner → Plan{tasks: [PlannedTask, …], confirmed_intent: …}
    ↓
For each PlannedTask:
    TaskTypeClassifier     rules-as-data; first match wins
    ContextResolver        project_type, available_personas, persona_load
    PersonaSelector        dispatch table: (task_type, context) → persona
    RoutingFallback        generic engineer OR LLM tie-break
    RoutingValidator       persona registered, enabled, has bandwidth
    ↓
RoutingTrace               input_hash, decision, trace[], duration_ms
```

## Layout

```
neumann/router/
├── __init__.py             # public API
├── types.py                # Shape, TaskType, PlannedTask, Plan, ConfirmedIntent, RoutingContext, …
├── shape_classifier.py     # mission vs single-task
├── interviewer.py          # Interviewer protocol + MockInterviewer + CLIInterviewer + validate_intent
├── task_classifier.py      # PlannedTask → TaskType
├── context_resolver.py     # default RoutingContext from target_files heuristics
├── persona_selector.py     # dispatch table lookup
├── validator.py            # pre-flight gate
├── fallback.py             # generic-engineer or LLM tie-break
├── registry.py             # persona id → metadata (Fusion presets + custom)
├── planner_protocol.py     # Planner protocol + MockPlanner for tests
├── pipeline.py             # RouterPipeline orchestrator
├── cli.py                  # `fnr` CLI (--interview flag)
├── rules/
│   ├── shape_rules.json
│   ├── interview_questions.json
│   ├── task_type_rules.json
│   └── persona_dispatch.json
└── personas/
    └── planner.json        # custom Planner persona spec (soul + instructions)
```

## CLI

```sh
# Classify a prompt's shape
fnr classify-shape "Fix the typo in the README"
# → shape: single-task  (priority 99, sentences=1)

# Route a single-task prompt (no interview)
fnr task "Add /api/healthz endpoint returning {ok:true}"
# → persona: backend-engineer
# → trace: task_type=backend, dispatch priority=1, validation: ok

# Run the interview before routing — recommended for any human-driven entry point
fnr --interview --allowed-org pakt-world task "Add /healthz endpoint"
# Asks: which repo? what's "done"? approve to write the plan?
# Then routes the confirmed intent.

# Plan + route a mission prompt (with interview)
fnr --interview --allowed-org pakt-world mission "Build the whole signup flow with email verification and onboarding"
# Same interview gate, then planner decomposes, then per-task routing.
```

The default planner is the offline `MockPlanner` (no fixtures), which produces a single-task plan derived from the prompt. To plug in a real LLM-backed planner, instantiate `RouterPipeline(planner=YourPlanner())`. Same shape for the interviewer: pass `interviewer=YourSlackInterviewer()` (etc.) to drive the Q&A through Slack threads / web chat / wherever the human is.

## Adding a new task type

1. Add a rule to `rules/task_type_rules.json`:
   ```json
   {
     "priority": 8,
     "pattern": "\\b(your|keywords|here)\\b",
     "match_fields": ["title", "description", "type_hints"],
     "type": "your-new-type"
   }
   ```
2. Add the enum value to `TaskType` in `types.py`.
3. Add dispatch rows in `rules/persona_dispatch.json`:
   ```json
   { "type": "your-new-type", "context": "*", "persona": "engineer", "priority": 1 }
   ```
4. Add a test in `tests/router/test_task_classifier.py`.

No code changes required for the kernel itself. Rules are data.

## Adding a new persona

1. Drop a JSON file in `neumann/router/personas/`:
   ```json
   {
     "id": "your-persona-id",
     "name": "Your Persona",
     "role": "custom",
     "description": "What this persona is responsible for.",
     "soul": "# Soul: Your Persona\n…",
     "instructionsText": "Operational checklist."
   }
   ```
2. The `PersonaRegistry` auto-loads `personas/*.json` on init. No code changes required.
3. To make the dispatch table route to it, add rows in `persona_dispatch.json` referencing the new id.

## Wiring into Fusion

Routing decisions print to stdout today. To actually create a Fusion task with the chosen persona, the dispatch step needs to call `fn task create --agent <persona-id> "..."`. That wiring is intentionally not in v1 because:

1. Persona records have to exist in Fusion's `agents` table before they can be assigned. The 9 presets are UI-only — they need to be materialized as agent rows.
2. The `assignedAgentId` field expects a Fusion-specific UUID, not the persona id used by the router. A bridge step (`persona_id → fusion_agent_id`) is needed.

See `MORNING_REVIEW.md` at the repo root for the wiring plan.

## Self-improvement loop (placeholder)

Future: each `RoutingTrace` gets logged to `~/.coywolf/router/log/decisions.jsonl`. When Brendan corrects a misrouting (or when a persona-task fails post-merge), the correction becomes a counter-example that gets folded back into `task_type_rules.json` as a new pattern. Today the trace is constructed and returned but not persisted — file-based logging will be added in v2.

## Tests

```sh
python3 -m pytest tests/router/ -v
# 66 passed in 0.07s
```

Test layers:
- `test_shape_classifier.py` — single-task vs mission
- `test_task_classifier.py` — PlannedTask → TaskType
- `test_persona_selector.py` — dispatch table behavior including unavailable-persona fallback
- `test_pipeline.py` — full end-to-end including MockPlanner fixtures
- `test_interviewer.py` — `validate_intent` schema gate, MockInterviewer fixtures, CLIInterviewer with stubbed I/O including refine path and max-rounds exhaustion
- `test_pipeline_with_interview.py` — pipeline end-to-end with the Interviewer wired in
