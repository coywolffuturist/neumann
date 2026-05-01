# Spec: QA Agent at the In Review Column

**Status:** Designed 2026-04-30 / 2026-05-01. NOT yet implemented.
**Ships across:** `coywolffuturist/neumann` (persona def + QA Test format) AND `coywolffuturist/coywolf` (fusion-watchdog L3 detector).

---

## Goal

Redefine Fusion's column semantics so In Review is the gate where an independent QA agent (Opus 4.7) executes a structured `## QA Test` section embedded in each task's `PROMPT.md`. Pass → task moves to Done. Fail → task moves back to In Progress with error context captured in the task log.

The agent is a different model class than the coder (Sonnet 4.6) so it has independent reasoning patterns and can't trivially rationalize the coder's choices. This is the "independent reviewer" property — load-bearing for the future autonomous swarm to self-supervise.

## Context

Brendan's column-semantic redefinition (2026-04-30):

| Column | Today's meaning | Brendan's redefinition | Who acts | Model |
|---|---|---|---|---|
| Planning | Planner spec'ing | Same | Planner persona | Opus 4.7 |
| Todo | "Specified, ready" | **Persona assigned, locked-in** | Neumann's PersonaSelector | (deterministic) |
| In Progress | Coder running | Same | Coder persona | Sonnet 4.6 |
| In Review | "Ready to merge" | **QA agent runs the embedded test** | QA persona | Opus 4.7 |
| Done | Merged | Code merged AND QA passed | — | — |

The In Review gate exists today as a Fusion `workflow_step` (Browser Verification — validated on FN-007). What's new:
- Generalize the gate beyond browser verification (also covers behavior tests, refactor equivalence, copy review, research soundness)
- Standardize the embedded test format so the QA agent can execute it deterministically
- Add a separate post-merge / post-deploy verification gate (live URL serves the change) as a fusion-watchdog L3 detector

References: `project_qa_agent_in_review.md`, `reference_fusion_model_assignment.md`, `reference_fusion_watchdog.md`, `feedback_micro_tasks_high_probability.md`.

## Acceptance criteria

1. **`## QA Test` schema documented.** Every PROMPT.md emitted by the Planner includes a structured QA Test section that the QA agent can execute mechanically. Schema below.
2. **QA persona definition** at `neumann/router/personas/qa.json` with system prompt biased toward independent skepticism. Provider: `pi-claude-cli`. Model: `claude-opus-4-7`.
3. **PersonaRegistry recognizes "qa"** and routes In Review tasks to it. Existing `qa-engineer` Fusion preset is the fallback display label; `qa` is the Neumann router persona id.
4. **Behavior verification:** when Fusion moves a task to In Review, the QA agent (a) loads the task's PROMPT.md, (b) extracts the `## QA Test` section, (c) executes each step, (d) reports pass/fail per step, (e) returns aggregate verdict. Pass → Fusion moves to Done. Fail → moves back to In Progress with error context.
5. **Post-deploy watchdog L3 detector** added to `~/.coywolf/scripts/fusion-watchdog.py` (in `coywolffuturist/coywolf`). Watches for task → Done events. For tasks tagged as UI/UX-touching, runs agent-browser checks against the live URL after merge + service restart. Pass = silent success. Fail = WhatsApp ping + automatic move back to In Progress.

## Architecture

### `## QA Test` schema (in PROMPT.md)

```markdown
## QA Test

**Type:** browser | behavior | refactor-equivalence | copy-review | research-soundness
**Reviewer model:** claude-opus-4-7
**Pass criterion:** all assertions pass

### Steps

1. Open the file `app/public/dashboard.html` in the worktree.
2. Verify line 4296 contains the new `pinnedNodeData` declaration.
3. Open `http://localhost:7777/<route>` via agent-browser.
4. Click the element matching selector `.intel-node`.
5. Assert: `document.getElementById('intel-graph-tooltip').style.display === 'block'`.
6. Assert: tooltip remains visible after `mouseout` from the node.
7. Click the tooltip itself.
8. Assert: `window.open` was called with the node's URL.

### Expected failure modes

- If the worktree's dashboard.html has no `pinnedNodeData` reference, this is a missing-implementation failure.
- If the tooltip vanishes on mouseout while pinned, this is a behavior-incorrect failure (most common bug).
```

Key properties:
- **Type tag** drives which executor the QA agent uses (browser → agent-browser; behavior → unit-test runner; refactor → diff-based equivalence; copy → tone/style review; research → source verification).
- **Steps are imperative + assertable** so the agent can execute them in sequence and produce a pass/fail per step.
- **Expected failure modes** are surfaced so the QA agent knows what to look for vs. what to report as new failure types.

### QA persona definition (`neumann/router/personas/qa.json`)

```json
{
  "id": "qa",
  "display_name": "QA Reviewer",
  "model_provider": "pi-claude-cli",
  "model_id": "claude-opus-4-7",
  "system_prompt_path": "personas/qa-system-prompt.md",
  "tool_allowlist": [
    "agent-browser",
    "Read",
    "Grep",
    "Bash"
  ],
  "tool_blocklist": [
    "Write",
    "Edit"
  ],
  "execution_mode": "review-only",
  "philosophy": "Independent skepticism. The coder may have rationalized their work; you have not seen their reasoning. Execute the QA Test exactly as written. Do not mark a step pass unless its assertion is mechanically verified. Report all failures with reproducible steps."
}
```

### Watchdog L3 (post-deploy verification)

In `~/.coywolf/scripts/fusion-watchdog.py`, add a third detector:

```python
def detect_post_deploy_drift(task, state):
    """L3: after task → done, verify the change is actually live on the deployed surface."""
    tid = task["id"]
    col = task.get("column")
    if col != "done":
        return None
    if state.get(tid, {}).get("post_deploy_verified"):
        return None  # already checked

    # Only fire for UI/UX-touching tasks (heuristic: target_files contains *.html / *.css)
    if not _touches_ui(task):
        return {tid: {"post_deploy_verified": True}}  # mark as not-applicable

    # Pull the QA Test from the task's PROMPT.md, run agent-browser against live URL
    qa_test = _load_qa_test(task)
    result = _run_qa_test_against_live(qa_test, base_url="http://localhost:7777")

    if result.passed:
        notify(f"🐺 Post-deploy QA: {tid} verified live ✓")
        return {tid: {"post_deploy_verified": True}}
    else:
        notify(f"🐺 Post-deploy QA FAIL: {tid} merged but not live — {result.summary}")
        api("POST", f"/api/tasks/{tid}/move", {"column": "in-progress"})
        return {tid: {"post_deploy_verified": False, "post_deploy_failure": result.summary}}
```

## Implementation steps

### Phase 1 — Schema + persona (Neumann)

1. Add `## QA Test` schema documentation to `neumann/docs/specs/qa-agent.md` (this file).
2. Create `neumann/router/personas/qa.json` with the persona definition.
3. Create `neumann/router/personas/qa-system-prompt.md` with the independent-skepticism system prompt (see Philosophy section above for tone).
4. Update `PersonaRegistry` to recognize "qa" persona id.
5. Update `persona_dispatch.json` so In Review tasks route to qa persona.
6. Update Planner persona's system prompt to ALWAYS emit a `## QA Test` section in PROMPT.md per the schema above.
7. Tests for schema parsing + persona routing.

### Phase 2 — Pre-merge QA executor (Fusion-side)

This phase requires either patching Fusion's bundled dist (like the step-extraction patch) OR building an external watcher. Decision deferred to Brendan.

If patch-Fusion approach: extend Fusion's `workflow_step` to support a generic "QA Test" step type that loads PROMPT.md, finds `## QA Test`, dispatches to QA persona, captures result.

If external-watcher approach: add a fusion-watchdog L3.5 (between zombie detection and post-deploy) that watches In Review column and runs QA Tests externally.

### Phase 3 — Post-deploy watchdog (Coywolf-side)

1. Add `detect_post_deploy_drift` to `~/.coywolf/scripts/fusion-watchdog.py`.
2. Wire the agent-browser invocation against `http://localhost:7777`.
3. State persistence: track `post_deploy_verified` per task in the watchdog state file.
4. Notification path: WhatsApp ping on failure, move task back to In Progress with `post_deploy_failure` annotation.

### Phase 4 — Edge cases

- Non-UI tasks (pure backend, refactors, docs) bypass post-deploy verification automatically.
- Tasks that fail QA twice in a row should escalate (paused + WhatsApp ping for human review) rather than infinite-looping in In Progress.
- The QA agent itself should never edit code (`Write`/`Edit` blocked) — only verify and report.

## Anti-patterns

- **Do NOT use the same model for QA as for coding.** Sonnet reviewing Sonnet's work tends to rationalize. Opus is the minimum class difference; cross-vendor would be even better but isn't currently allowed (Claude Max only — see `feedback_claude_max_only.md`).
- **Do NOT let the QA agent edit the worktree.** It's a reviewer, not a fixer. If the test fails, it reports back to the coder via task log; the coder fixes.
- **Do NOT skip the post-deploy check for UI tasks.** Pre-merge browser verification runs in the worktree, not the live server. They're different gates.
- **Do NOT have the QA Test run automated tests as a substitute for the embedded checklist.** `pytest -q` is fine as ONE step in the QA Test, but the structured human-readable checklist is what makes the gate auditable.

## Open questions

- Should QA Test failures retry automatically (e.g., 1 retry with fresh executor) before bouncing to In Progress? Tradeoff: catches transient failures vs. masks real bugs.
- Should the Planner's QA Test generation be reviewed by a human before tasks land in Todo? Adds friction; reduces wasted executor cycles on bad QA Tests. Current: ship without human pre-review, learn what fails, iterate.
- Where does the post-deploy verification live during the v1 implementation — Fusion's source patch, or external watchdog? Watchdog is faster to ship and survives Fusion upgrades; patch is the "real" answer long-term.

## References

- Memory: `project_qa_agent_in_review.md`, `reference_fusion_model_assignment.md`, `reference_fusion_watchdog.md`, `reference_fusion_step_extraction_patch.md`, `feedback_claude_max_only.md`, `feedback_micro_tasks_high_probability.md`
- Related specs: `pipeline-ordering.md` (the Planner that emits `## QA Test` is the same Planner this spec depends on)
- Cross-repo: `coywolffuturist/coywolf` for the watchdog L3 portion
