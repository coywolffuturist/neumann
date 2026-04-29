# neumann.router

> Deterministic persona routing for AI agent systems. Plug Neumann's symbolic kernel between an LLM planner and downstream specialists.

## What it is

A subpackage of [Neumann](../../README.md) that routes planned tasks to the right specialist persona via dispatch tables — no LLM call required for routing itself.

The split:
- **LLM generates.** A planner (Claude / Sonnet / etc.) takes a user prompt and produces a structured `Plan` with one or more `PlannedTask`s.
- **Neumann routes.** Each `PlannedTask` flows through a deterministic pipeline: classify → resolve context → select persona → validate → fallback if needed.

## Why this shape

Routing on raw prompts forces routing decisions on under-specified prose — the same problem Neumann was built to solve. Routing on **structured planned tasks** lets the dispatch rules lean on `target_files`, `type_hints`, and `acceptance_criteria` — far stronger signals than NL keyword matches.

## Pipeline

```
User prompt
    ↓
ShapeClassifier         single-task | mission
    ↓
[mission] → Planner → Plan{tasks: [PlannedTask, …]}
    ↓
For each PlannedTask:
    TaskTypeClassifier  rules-as-data; first match wins
    ContextResolver     project_type, available_personas, persona_load
    PersonaSelector     dispatch table: (task_type, context) → persona
    RoutingFallback     generic engineer OR LLM tie-break
    RoutingValidator    persona registered, enabled, has bandwidth
    ↓
RoutingTrace            input_hash, decision, trace[], duration_ms
```

## Layout

```
neumann/router/
├── __init__.py             # public API
├── types.py                # Shape, TaskType, PlannedTask, Plan, RoutingContext, …
├── shape_classifier.py     # mission vs single-task
├── task_classifier.py      # PlannedTask → TaskType
├── context_resolver.py     # default RoutingContext from target_files heuristics
├── persona_selector.py     # dispatch table lookup
├── validator.py            # pre-flight gate
├── fallback.py             # generic-engineer or LLM tie-break
├── registry.py             # persona id → metadata (Fusion presets + custom)
├── planner_protocol.py     # Planner protocol + MockPlanner for tests
├── pipeline.py             # RouterPipeline orchestrator
├── cli.py                  # `fnr` CLI
├── rules/
│   ├── shape_rules.json
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

# Route a single-task prompt
fnr task "Add /api/healthz endpoint returning {ok:true}"
# → persona: backend-engineer
# → trace: task_type=backend, dispatch priority=1, validation: ok

# Plan + route a mission prompt
fnr mission "Build the whole signup flow with email verification and onboarding"
# → invokes planner, dispatches each planned task
```

The default planner is the offline `MockPlanner` (no fixtures), which produces a single-task plan derived from the prompt. To plug in a real LLM-backed planner, instantiate `RouterPipeline(planner=YourPlanner())`.

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
# 49 passed in 0.06s
```

Test layers:
- `test_shape_classifier.py` — single-task vs mission
- `test_task_classifier.py` — PlannedTask → TaskType
- `test_persona_selector.py` — dispatch table behavior including unavailable-persona fallback
- `test_pipeline.py` — full end-to-end including MockPlanner fixtures
