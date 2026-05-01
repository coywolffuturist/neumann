# Spec: QA Agent at the In Review Column

**Status:** Designed 2026-04-30. Revised 2026-05-01 per Brendan's review feedback. **APPROVED 2026-05-01** — ready for Phase 1 implementation in next session. Implementation deferred from current session due to context window — see `session_handoff_2026-05-01.md` for resumption instructions.
**Ships across:** `coywolffuturist/neumann` (persona defs + QA Test format) AND `coywolffuturist/coywolf` (fusion-watchdog post-deploy detector + Coywolf-driven async QA).

---

## Goal

Redefine Fusion's column semantics so In Review is a real verification gate, not just "ready to merge." Each task's `PROMPT.md` carries a structured `## QA Test` section that the Planner authored at planning time. When the task moves to In Review, an **independent QA agent** executes that test mechanically and either passes the work to Done or bounces it back to In Progress with error context.

After merge, a **second QA tier** (Coywolf, running locally on the Mac Mini with a different model family) re-runs the same test against the live deployed URL — catching changes that merged but didn't actually deploy live, plus drift that surfaces hours/days after merge.

This gives the system three properties critical for the future autonomous swarm:
1. **Test criteria pre-committed at planning time** — same agent that writes the spec writes the test, so "done" is defined before any code is written. The QA agent cannot rationalize tests around what the coder produced.
2. **Independent reviewer property** — pre-merge QA uses a different model *class* than the coder (Opus reviewing Sonnet); post-deploy QA uses a different model *vendor* (Qwen reviewing Anthropic).
3. **Self-healing loop** — when post-deploy QA detects regression, Coywolf can auto-file a Fix task into Lucid's intake (gated by human approval at the intake step so autonomy stays bounded).

---

## Context

Brendan's column-semantic redefinition (2026-04-30, model assignments hardcoded 2026-05-01):

| Column | Today's meaning | Brendan's redefinition | Who acts | Model (hardcoded) |
|---|---|---|---|---|
| Planning | Planner spec'ing | Same | Planner persona | `claude-opus-4-7` |
| Todo | "Specified, ready" | **Persona assigned, locked-in** | Neumann's PersonaSelector + Haiku tiebreak when rules tie | `claude-haiku-4-5` (tiebreak only) |
| In Progress | Coder running | Same | Coder persona | `claude-sonnet-4-6` |
| In Review | "Ready to merge" | **Pre-merge QA runs the embedded test against the worktree** | QA persona (synchronous gate) | `claude-opus-4-7` |
| Done | Merged | **Post-deploy QA verifies the change is actually live** | Coywolf cron (asynchronous monitor) | Qwen 3.6 via Ollama (local, cross-vendor) |

The In Review gate exists today as a Fusion `workflow_step` (Browser Verification — validated on FN-007). What's new:
- Generalize the gate beyond browser verification (also covers behavior tests, refactor equivalence, copy review, research soundness)
- Standardize the embedded test format so any QA agent can execute it deterministically
- Add a separate **post-deploy** verification tier (Coywolf-driven, cross-vendor, async)
- Make retry / escalation a core gate behavior, not Phase-4 polish

References: `project_qa_agent_in_review.md`, `reference_fusion_model_assignment.md`, `reference_fusion_watchdog.md`, `feedback_micro_tasks_high_probability.md`, `feedback_clawd_browser_relay.md`, `reference_browser_use_tools.md`.

---

## The two verification tiers

```
Task lifecycle in Fusion:
  Planning → Todo → In Progress → [In Review] → Done → [Coywolf post-deploy QA]
                                       ↑                          ↓
                                       │                  on fail: auto-file Fix
                                       │                  via Lucid intake
                                       │
                                  on pre-merge fail
                                  (after N retries):
                                  pause + WhatsApp ping
```

### Tier 1 — Pre-merge QA (synchronous, in-Fusion-pipeline)

| Property | Value |
|---|---|
| When | Task moves to In Review |
| Where the test runs | The worktree (changes present, NOT yet merged) |
| Who runs the test | QA persona (`claude-opus-4-7` via pi-claude-cli) |
| What it verifies | Code does what its `## QA Test` claims it does — *behavior is correct in the worktree* |
| Outcome | Pass → merge + move to Done. Fail → see retry/escalation below. |
| Why this model | Different class than coder (Sonnet 4.6); independent reasoning patterns reduce rationalization risk |

### Tier 2 — Post-deploy QA (asynchronous, Coywolf-driven, cross-vendor)

| Property | Value |
|---|---|
| When | Periodic cron + immediate trigger after merge events |
| Where the test runs | The live URL (e.g. `https://lucid.newangeles.xyz/...`, `http://localhost:7777/...`) |
| Who runs the test | Coywolf agent on Mac Mini, using Qwen 3.6 via Ollama (local, free per-token) |
| What it verifies | The merged change is actually serving live — catches deploy-skip silently, config drift, service-restart failures, third-party regressions |
| Outcome | Pass → silent success (state file updated). Fail → WhatsApp ping AND auto-file a Fix task via Lucid's intake API. |
| Why this model | Different vendor than coder (Qwen vs Anthropic); true cross-vendor independence; runs continuously at zero per-token cost |
| Caveat | Qwen tool-use is weaker than Claude. Coywolf's job here is constrained: execute a pre-written `## QA Test` checklist. NOT open-ended tool reasoning. |

---

## Retry / escalation (core gate behavior)

Both tiers honor the same retry contract:

| Failure count | Action |
|---|---|
| 1st fail | Move task back to In Progress with error context appended to task log. Coder re-runs with the failure detail in scope. |
| 2nd fail | Same as 1st (one more attempt). |
| **3rd fail (= 2 retries exhausted)** | **Pause task. WhatsApp ping Brendan with: task ID, QA tier (pre-merge / post-deploy), each round's failure summary.** Brendan reviews and either un-pauses with new context (e.g. modified PROMPT.md) or kills the task. |
| Subsequent | Should never reach here — task is paused. |

The threshold (default `max_qa_retries = 2`) lives in `~/.fusion/config.json` so it's per-environment tunable. Each retry's failure context is appended to `task.log[]` so future agents (or future Brendan) can pattern-match on the failure modes.

For post-deploy regressions specifically: instead of bouncing back to In Progress (the original task may have been done correctly), Coywolf auto-files a NEW Fix task in Lucid's intake with the regression description. Brendan still gates the Fix at the intake-approval step — autonomy is "Coywolf can SUGGEST a Fix" not "Coywolf can ship code without approval."

---

## Browser-interaction policy (hardcoded)

For QA tasks of `Type: browser`:

- **Real Chrome on the Mac Mini's GUI session, NEVER headless.** Anti-bot detection treats programmatic browsers differently; QA tests must reflect what a real user sees, not what a bot sees. Real browser also catches layout overflow, font fallback, animation glitches that headless misses.
- **Use `agent-browser --show`** as the default tool. Visible window, real Chrome, attached to the GUI session. Available at `/opt/homebrew/bin/agent-browser` on the Mac Mini.
- **Optional escalation to CuaDriver** when pixel-level / accessibility-tree interaction matters (drag-drop, system dialogs, keyboard-shortcut UX, CAPTCHAs that block CDP-driven interaction). CuaDriver lives at `/opt/homebrew/bin/cuadriver` per `reference_browser_use_tools.md`.
- **Hard ban on Clawd Browser Relay.** Per `feedback_clawd_browser_relay.md` — does not work and shall never be invoked. Any QA Test that names it is a planner bug.
- **Single-threaded browser concurrency** — only one QA test runs against a given browser session at a time. Multiple in-progress QA jobs queue rather than fan out.

---

## Architecture

### `## QA Test` schema (in PROMPT.md)

```markdown
## QA Test

**Type:** browser | behavior | refactor-equivalence | copy-review | research-soundness
**Reviewer tier:** pre-merge | post-deploy | both
**Pre-merge model:** claude-opus-4-7
**Post-deploy model:** qwen-3.6 (Coywolf cron)
**Pass criterion:** all assertions pass
**Browser tool:** agent-browser --show  (NEVER headless, NEVER Clawd Relay)

### Steps

1. Open the file `app/public/dashboard.html` in the worktree.
2. Verify line 4296 contains the new `pinnedNodeData` declaration.
3. Open `http://localhost:7777/<route>` via `agent-browser --show`.
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
- **Reviewer tier tag** says whether the test runs pre-merge, post-deploy, or both. Most browser tests should be `both` — pre-merge catches the obvious, post-deploy catches the deployed state.
- **Steps are imperative + assertable** so the agent can execute them in sequence and produce a pass/fail per step.
- **Expected failure modes** surfaced so the QA agent knows what to look for vs. what to report as a new failure type.

### Pre-merge QA persona (`neumann/router/personas/qa.json`)

```json
{
  "id": "qa",
  "display_name": "QA Reviewer (pre-merge)",
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
    "Edit",
    "Clawd Browser Relay"
  ],
  "execution_mode": "review-only",
  "philosophy": "Independent skepticism. The coder may have rationalized their work; you have not seen their reasoning. Execute the QA Test exactly as written. Do not mark a step pass unless its assertion is mechanically verified. Report all failures with reproducible steps. Browser tests run in agent-browser --show against real Chrome — never headless, never Clawd Relay."
}
```

### Coywolf post-deploy QA agent (`~/.coywolf/scripts/coywolf-qa.py`)

A Python script that runs as a launchd cron job (every N minutes) on the Mac Mini. Reads recently-merged tasks from Fusion's daemon API, executes each task's `## QA Test` against the live URL, dispatches sub-reads to Qwen via Ollama, and on failure auto-files a Fix task via Lucid's intake API.

```python
# Pseudo-code
def main():
    recently_merged = fusion_api("GET /api/tasks?column=done&since=10min")
    for task in recently_merged:
        if task.id in already_verified():
            continue
        qa_test = parse_qa_test(task.prompt_md)
        if qa_test.reviewer_tier not in ("post-deploy", "both"):
            mark_verified(task.id, "skipped: not post-deploy scope")
            continue
        result = run_qa_test_via_qwen(qa_test, base_url=task.live_url)
        if result.passed:
            mark_verified(task.id, "passed")
            continue
        notify_whatsapp(f"🐺 Post-deploy QA FAIL: {task.id} — {result.summary}")
        # Auto-file a Fix task via Lucid intake (which gates at human approval)
        lucid_api("POST /api/intake/start", {
            "category": "fix",
            "intent": f"Post-deploy regression on {task.id}: {result.summary}",
            "auto_filed_by": "coywolf-qa",
            "parent_task_id": task.id,
        })
        mark_verified(task.id, "failed_filed_fix")
```

State file: `~/.coywolf/state/coywolf-qa-state.json` tracks `{task_id: {verified_at, status, retry_count}}`.

### PersonaRegistry update

Add "qa" persona id to `neumann/router/registry.py`. Existing `qa-engineer` Fusion preset becomes the display label.

### Planner system prompt update

Update `neumann/router/personas/planner-system-prompt.md` to ALWAYS emit a `## QA Test` section in PROMPT.md per the schema above. The QA Test must be specific enough that an agent (Opus or Qwen) executing the steps produces a deterministic pass/fail.

---

## Acceptance criteria

1. `## QA Test` schema documented in this spec and in `neumann/router/personas/planner-system-prompt.md`.
2. Planner persona always emits a `## QA Test` section with `Type`, `Reviewer tier`, `Pre-merge model`, `Post-deploy model`, `Pass criterion`, `Browser tool`, `Steps`, and `Expected failure modes`.
3. QA persona at `neumann/router/personas/qa.json` exists with the spec'd config (Opus 4.7, tool allowlist, blocklist, philosophy).
4. PersonaRegistry recognizes "qa" persona id; `persona_dispatch.json` routes In Review column to it.
5. Pre-merge QA execution: when Fusion moves a task to In Review, the QA agent loads PROMPT.md, extracts QA Test, executes each step, reports per-step pass/fail, returns aggregate verdict. Pass → Fusion moves to Done. Fail → moves back to In Progress with error context.
6. Retry/escalation contract: 2 retries (configurable via `max_qa_retries` in `~/.fusion/config.json`), then auto-pause + WhatsApp ping with task ID + tier + failure summary.
7. Post-deploy QA: `~/.coywolf/scripts/coywolf-qa.py` exists and runs as launchd cron (every 5 min). Reads recently-merged tasks, executes post-deploy-tagged QA Tests against live URLs via Qwen 3.6 (Ollama), auto-files Fix tasks via Lucid's intake on failure.
8. Browser interactions: agent-browser invoked with `--show` flag (visible window, real Chrome). NEVER headless. NEVER Clawd Browser Relay (enforced via tool_blocklist on QA persona).
9. State persistence: pre-merge retry count tracked in `task.extra["qa_retry_count"]`; post-deploy verification tracked in `~/.coywolf/state/coywolf-qa-state.json`.
10. Tests for the QA Test parser (extract structured section from PROMPT.md) and the retry/escalation contract.

---

## Implementation steps

### Phase 1 — Schema + personas + Planner update (Neumann)

1. Document the `## QA Test` schema in `neumann/router/personas/planner-system-prompt.md` (extend existing planner prompt).
2. Create `neumann/router/personas/qa.json` with the persona definition.
3. Create `neumann/router/personas/qa-system-prompt.md` with the independent-skepticism prompt.
4. Update `PersonaRegistry` to recognize "qa" persona id.
5. Update `persona_dispatch.json` so In Review column routes to qa persona.
6. Add a `parse_qa_test(prompt_md: str) -> QATest` parser to `neumann/router/qa_test.py` (returns structured object: type, reviewer_tier, pre_merge_model, post_deploy_model, browser_tool, steps[], expected_failures[]).
7. Tests: `tests/router/test_qa_test_parser.py` covers happy path, missing fields, malformed steps.

### Phase 2 — Pre-merge QA executor (in-Fusion-pipeline, synchronous)

Decision point: patch Fusion's bundled dist OR run as external watcher. Brendan to choose.

**If patch-Fusion route:**
- Extend Fusion's `workflow_step` to support a generic "QA Test" step type that loads PROMPT.md, extracts QA Test, dispatches to QA persona, captures result, applies retry contract.
- Add patch to `~/.coywolf/patches/apply-fusion-qa-step-patch.sh` following the precedent from `reference_fusion_step_extraction_patch.md`.

**If external-watcher route:**
- Add a fusion-watchdog detector layer that watches In Review column entries and runs QA Tests externally before letting them progress to Done.
- Cleaner from a "don't touch Fusion internals" perspective; more brittle in terms of ordering guarantees.

### Phase 3 — Post-deploy QA (Coywolf-driven, async, cross-vendor)

1. Create `~/.coywolf/scripts/coywolf-qa.py` (Python, runs as launchd cron).
2. Create `~/Library/LaunchAgents/com.coywolf.qa.plist` (every 5 min trigger, KeepAlive=false, RunAtLoad=true).
3. Implement Qwen 3.6 invocation via Ollama HTTP API (`http://localhost:11434/api/chat` or similar).
4. Implement `parse_qa_test` import from a Python port of Neumann's parser, OR shell out to `python -m neumann.router.qa_test`.
5. Implement Lucid intake API call to auto-file Fix tasks on regression detection.
6. State file at `~/.coywolf/state/coywolf-qa-state.json` for verification history.
7. WhatsApp ping integration via existing clawdbot CLI.

### Phase 4 — Polish

1. QA Test viewer in Lucid (read-only display of the structured QA Test alongside the task in fusion-ops UI).
2. Per-environment retry threshold tuning.
3. QA Test failure-pattern dashboard (which step types fail most? which task types over-spec? which under-spec?).

---

## Anti-patterns

- **Do NOT use the same model for QA as for coding.** Sonnet reviewing Sonnet's work tends to rationalize. Opus is the minimum class difference (pre-merge); Qwen is the minimum vendor difference (post-deploy).
- **Do NOT let any QA agent edit the worktree.** They're reviewers, not fixers. If a test fails, they report; the coder fixes. This is hard-enforced via the `tool_blocklist` on the QA persona.
- **Do NOT skip the post-deploy check for UI tasks.** Pre-merge browser verification runs in the worktree, not the live server. They're different gates measuring different properties.
- **Do NOT have the QA Test substitute automated tests for the structured human-readable checklist.** `pytest -q` is fine as ONE step in the QA Test, but the structured checklist is what makes the gate auditable and replayable across QA tiers.
- **Do NOT use headless browsers for QA Tests.** Anti-bot detection differs from real Chrome; layout/render bugs are missed. Hard rule: agent-browser `--show`, never `--headless`.
- **Do NOT use Clawd Browser Relay.** Banned per `feedback_clawd_browser_relay.md`. Hardcoded into qa.json's `tool_blocklist`.
- **Do NOT let Coywolf auto-fix code directly.** Coywolf can FILE a Fix task in Lucid's intake; Brendan approves at the intake step. Autonomy boundary is "suggest" not "ship."
- **Do NOT extend Qwen's role beyond constrained checklist execution.** Qwen tool-use is weaker than Claude; QA Test execution is well within capability, but open-ended tool reasoning is not.

---

## Open questions

- Pre-merge executor mechanism: patch-Fusion vs external-watcher. Brendan to decide. Default lean: external-watcher first for speed-to-ship + Fusion-upgrade-survivability; patch later if needed.
- Should retry-1 use a different prompt than retry-2 (escalating "you have failed before, try a meaningfully different approach")? Probably yes; deferring detail to implementation.
- Cron cadence for Coywolf post-deploy QA: 5 min default. Tune as we see real volume.
- Should Coywolf's auto-filed Fix tasks be pre-flagged in Lucid's intake as "regression — needs human approval"? Yes; defer to Lucid intake panel spec to expose this affordance.

---

## References

- Memory: `project_qa_agent_in_review.md`, `reference_fusion_model_assignment.md`, `reference_fusion_watchdog.md`, `reference_fusion_step_extraction_patch.md`, `feedback_claude_max_only.md`, `feedback_micro_tasks_high_probability.md`, `feedback_clawd_browser_relay.md`, `reference_browser_use_tools.md`
- Related specs: `pipeline-ordering.md` (the Planner that emits `## QA Test` is the Planner this spec depends on)
- Cross-repo: `coywolffuturist/coywolf` for Coywolf-QA + watchdog plumbing
- Cross-repo: `coywolffuturist/lucid` for the intake API Coywolf uses to auto-file Fix tasks (see `lucid/docs/specs/intake-panel.md`)
