# Morning review — `feature/router` branch

**Built overnight, 2026-04-29. Updated morning of 2026-04-29 with Interviewer module per Brendan's spec.** Everything below is on the local branch `feature/router`. Nothing has been pushed. Test it, then tell me whether to push or revise.

## Update — Interviewer added (interview → plan → route)

Per Brendan's morning addition: a clarifying interview must happen between the user's prompt and the LLM Planner. The agent doesn't speculate; the human explicitly confirms the agent's understanding before any plan is written. Repo targeting is one of the mandatory fields.

New components:
- `neumann/router/interviewer.py` — `Interviewer` Protocol + `MockInterviewer` (tests) + `CLIInterviewer` (interactive terminal) + `validate_intent` (deterministic schema gate)
- `neumann/router/rules/interview_questions.json` — required-field gates: target_repo, success_criteria, human_approved (mandatory); out_of_scope, constraints (optional, asked by LLM-driven interviewers when appropriate)
- New `ConfirmedIntent` and `InterviewExchange` types in `types.py`
- `RouterPipeline` accepts `interviewer=…` and `allowed_orgs=…`. When wired, every prompt passes through the interview before planning/routing.
- `Plan.confirmed_intent` links the plan back to the interview that authorized it.
- `cli.py` gains `--interview` flag and `--allowed-org` repeatable flag.

Demo of the new flow:
```
$ printf 'pakt-world/paktsuite-v2\nGET /healthz returns 200 with {ok:true}\napprove\n' | \
    fnr --interview --allowed-org pakt-world task "Add /healthz endpoint"

[router-interview] Which repository should this work land in? Allowed: (none configured).
> pakt-world/paktsuite-v2
[router-interview] What does 'done' look like? Be specific — I'll use this to verify when the change is ready.
> GET /healthz returns 200 with {ok:true}
[router-interview] Here's my understanding:
  Intent: Add /healthz endpoint
  Repo: pakt-world/paktsuite-v2
  Done when: GET /healthz returns 200 with {ok:true}
Approve to write the plan? (yes / refine: ...)
> approve

confirmed intent: Add /healthz endpoint
  target repo:     pakt-world/paktsuite-v2
  success:         GET /healthz returns 200 with {ok:true}
  approved:        True
task 1: Add /healthz endpoint
  → persona:  backend-engineer
  → type:     backend
  → trace: matched dispatch row priority=1 → backend-engineer; validation: ok
```

Test count: **66 passing** (was 49). New layers: `test_interviewer.py` (validate_intent gate, MockInterviewer fixtures, CLIInterviewer including refine path + max-rounds exhaustion), `test_pipeline_with_interview.py` (end-to-end with interview wired).

## What's in the branch

A new `neumann/router/` subpackage that takes Neumann's symbolic-routing pattern and applies it to **persona selection** for AI agent systems (Fusion / Pi). The split is the same: LLM generates (planner), Neumann routes (deterministic dispatch).

```
neumann/router/
├── __init__.py
├── types.py                # Shape, TaskType, PlannedTask, Plan, RoutingContext, …
├── shape_classifier.py     # single-task | mission
├── task_classifier.py      # PlannedTask → TaskType
├── context_resolver.py     # infer project_type from target_files
├── persona_selector.py     # dispatch table lookup
├── validator.py            # persona registered + enabled + has bandwidth
├── fallback.py             # generic engineer OR LLM tiebreak callback
├── registry.py             # 9 Fusion presets + auto-loaded custom personas/
├── planner_protocol.py     # Planner Protocol + MockPlanner
├── pipeline.py             # RouterPipeline (composes everything)
├── cli.py                  # `fnr` — classify-shape | task | mission
├── README.md
├── rules/
│   ├── shape_rules.json    # mission detector
│   ├── task_type_rules.json
│   └── persona_dispatch.json
└── personas/
    └── planner.json        # custom Planner persona spec

tests/router/
├── test_shape_classifier.py
├── test_task_classifier.py
├── test_persona_selector.py
└── test_pipeline.py
```

49 tests pass:

```sh
cd /tmp/neumann
python3 -m pytest tests/router/ -v
# ============================== 49 passed in 0.06s ==============================
```

## Quick demo

```sh
$ python3 -m neumann.router.cli classify-shape "Fix the typo in the README"
shape: single-task  (priority 99, sentences=1)

$ python3 -m neumann.router.cli classify-shape "Build the whole signup flow with verification and onboarding"
shape: mission  (priority 1, sentences=0)
note:  Explicit multi-piece intent — 'build the whole signup flow', …

$ python3 -m neumann.router.cli task "Add /api/healthz endpoint returning {ok:true}"
task 1: Add /api/healthz endpoint returning {ok:true}
  → persona:  backend-engineer
  → type:     backend
  → trace:
      task_type=backend
      context.project_type=*
      matched dispatch row priority=1 → backend-engineer
      validation: ok
```

## What it does NOT do yet (intentional)

1. **No real LLM planner.** Default `MockPlanner` produces a single-task plan derived from the raw prompt. Real Claude/Sonnet planner integration is a follow-up: implement the `Planner` Protocol (one method, `plan(prompt) -> Plan`), inject via `RouterPipeline(planner=…)`. The Planner persona soul + instructionsText already lives in `personas/planner.json` and explicitly instructs the LLM to emit JSON matching the `Plan` shape — so the integration layer is small.

2. **No Fusion `fn task create` wiring.** The CLI prints decisions; it doesn't yet shell out to `fn`. Two reasons:
   - The 9 preset personas are UI-only in Fusion — they need to be materialized as rows in the `agents` table before `assignedAgentId` can reference them.
   - `assignedAgentId` expects Fusion's UUID, not the router's persona id. A `persona_id → fusion_agent_id` bridge needs to land before we plug in.
   Plan is below.

3. **No persistent decision log.** Every routing decision builds a `RoutingTrace` with `input_hash` + `trace[]` + `duration_ms`, but they're returned to the caller, not written to disk. Adding `~/.coywolf/router/log/decisions.jsonl` is a small follow-up; deferred so we can get the schema right with you in the morning.

4. **No automatic rule promotion from outcomes.** The self-improvement loop (mis-routing correction → new rule) is documented but not implemented. It's a v2.

## Wiring plan (for review)

To go from "router prints decisions" to "router creates Fusion tasks with the right persona":

### Step 1 — Materialize the 9 presets in Fusion's `agents` table

Currently the presets live in the client bundle (`AgentsView-*.js`). They need to exist as `agents` rows so `assignedAgentId` can point at them.

Mac-Mini-side script (sketch):
```python
import json, sqlite3, secrets
from datetime import datetime, timezone
DB = "/Users/coywolfden/coywolf/repos/lucid/.fusion/fusion.db"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

PRESETS = {
  "qa-engineer": {
    "name": "QA Engineer",
    "role": "engineer",
    "soul": "# Soul: Quality Assurance Engineer\n…",  # full text from AgentsView bundle
    "instructionsText": "Always run the full test suite…",
  },
  # … rest of 9 …
}
conn = sqlite3.connect(DB)
for pid, spec in PRESETS.items():
    aid = "agent-" + secrets.token_hex(8)
    conn.execute(
      "INSERT OR IGNORE INTO agents (id, name, role, state, taskId, createdAt, updatedAt, metadata, data) VALUES (?, ?, ?, 'idle', NULL, ?, ?, ?, ?)",
      (aid, spec["name"], spec["role"], NOW, NOW, '{}',
       json.dumps({"id": aid, **spec})),
    )
conn.commit()
```

The trick: writing the `data` blob with the full soul + instructionsText so `hasAgentIdentity()` returns true — that's what makes the persona "real" enough for Pi to layer it into prompts.

### Step 2 — Build a `persona_id → fusion_agent_id` lookup

Either:
- **Tag-based:** add a `tag` column to `agents`, set `tag = 'qa-engineer'` on the QA agent record. Router resolves tag → id.
- **Convention-based:** store the Fusion `agent.id` *as* the persona id (`qa-engineer`, not a UUID). Skip the lookup entirely.

Convention-based is cleaner. Just use the persona id as the Fusion agent id when materializing in step 1.

### Step 3 — Add an `fn` shell-out from `cli.py`

Replace the print-only path with:
```python
subprocess.run([
  "fn", "-P", project_name, "task", "create", task.title,
  "--agent", decision.persona,
])
```
Add `--description`, `--type-hints`, etc. once we confirm `fn task create`'s flag surface.

### Step 4 — Wire 2touch's Slack listener to call `fnr` instead of bare `fn`

Currently the Slack flow in `app/server.js` directly INSERTs into `code_requests`. After step 3, it should:
1. Call `fnr task` (or `fnr mission`) with the prompt.
2. Read back the decisions.
3. Create one or more code_requests, each tagged with the chosen persona.

This is also where the **2touch Approval gate** stays — Brendan still approves before claude actually runs. The router just chooses *which persona's claude* runs.

## Open questions for the morning

1. **Scope of router:** does it apply only to Lucid (your personal Fusion projects) or also to 2touch (Pakt-team Slack flow)? My read is *both* — different rule files per project, same kernel. Confirm.
2. **Rule curation cadence:** how often do you want to review the rule diff? My instinct: rules live in this repo, PR-reviewed like code, no auto-merge of rule changes.
3. **The `__fallback__` LLM tie-break path** is wired but defaults to "generic engineer." When you want it to actually invoke an LLM, pass a `tiebreak_callback` to `RoutingFallback`. Want me to draft a `claude-haiku` callback or leave it pluggable?
4. **The Planner persona itself** lives in `personas/planner.json` with a complete soul + instructionsText. Read it (it's short) and tell me if the schema it commits to matches what you want from a real LLM planner. Tightening the schema makes the deterministic post-route stage more reliable.

## Branch state

- Branch: `feature/router` off `main` (commit `fd6b9a3`)
- Files added: 23 new files, 0 modified
- Tests: 49 passing, 0 failing
- Not pushed.

To push when you're ready:
```sh
cd /tmp/neumann
git push -u origin feature/router
gh pr create --title "feat: deterministic persona-routing kernel" --body "see MORNING_REVIEW.md"
```

To trash and rebuild:
```sh
cd /tmp/neumann
git checkout main && git branch -D feature/router
```

Brewing coffee for you. ☕
