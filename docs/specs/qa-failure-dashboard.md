# Spec: QA Failure-Pattern Dashboard (Phase 4)

**Status:** Designed 2026-05-01. Implementation deferred. Write is a Phase-4 polish item per `qa-agent.md`; lands once Phases 2 + 3 have produced enough live verdicts to make the dashboard worth building.

**Ships in:** `coywolffuturist/lucid` as a read-only fusion-ops sub-page, OR as a stand-alone TUI in `coywolffuturist/coywolf`. Decision deferred to first user.

---

## Goal

Surface QA Test failure patterns from the verdict stream so Brendan (and future agents) can spot:

1. **Over-spec'd tasks** — Type=browser tests that never fail at the live URL but always fail pre-merge. Suggests the planner is writing tests too tight for the worktree's harness, or expecting state the coder isn't responsible for.
2. **Under-spec'd tasks** — tests that pass pre-merge AND post-deploy but the user reports a regression anyway. Suggests the planner is writing tests too loose; missing assertions.
3. **Drift sources** — task types where post-deploy QA fails meaningfully more often than pre-merge QA. The deploy path is the bug, not the code.
4. **Planner-bug clusters** — categories of `## QA Test` sections that the parser keeps rejecting. Surface so the Planner persona can be tuned.
5. **Reviewer disagreements** — same task PASS pre-merge, FAIL post-deploy. The cross-vendor independence the architecture pays for is exactly this signal; surface the disagreement set.

---

## Inputs

Two existing data sources from Phases 2 + 3:

### Pre-merge verdict log (Phase 2)

Source: Fusion task log (`task.log[]`) appended by `FusionWatcher` on every dispatch. Each retry-bounce or pause records:
- task_id, attempt_n, verdict, summary, failed_steps[], reproducible_context

The watcher already writes these (see `_format_failure_context` in `fusion_watcher.py`). Aggregating is a read-only query against Fusion's task store.

### Post-deploy verdict log (Phase 3)

Source: `~/.coywolf/state/coywolf-qa-state.json`. Each record:
- task_id, verified_at, status (`passed | failed_filed_fix | failed_no_fix | skipped | planner_bug`), summary, fix_task_id

Already atomically maintained by `coywolf_qa.StateStore`.

---

## Aggregations

| View | Groups by | Surfaces |
|---|---|---|
| Failure rate by Type | `qa_test.type` (browser / behavior / refactor-equivalence / copy-review / research-soundness) | Which test types fail the most. Hint at planner-tuning targets. |
| Failure rate by Reviewer tier | pre-merge / post-deploy | If post-deploy >> pre-merge → deploy path is the bug source. |
| Pre-merge vs post-deploy disagreement | task_id with PASS pre-merge AND FAIL post-deploy (or inverse) | High-signal reviewer-disagreement set. |
| Repeat planner bugs | normalized parser error message | Categories the Planner persona keeps producing. Tune the planner-system-prompt with examples. |
| Step failure heatmap | step number within QA Test | Which step in the average test fails. (Likely the ones doing assertions on state set by side effects.) |
| Time-to-pass | task_id | Distribution of how many retries it takes to pass. Long tail = under-decomposed tasks. |

---

## UI

Lean toward Lucid sub-page over TUI:

- Lucid already has the fusion-ops admin page Brendan added earlier. Adding a `/fusion-ops/qa-stats` route reuses that auth + layout chrome.
- Read-only: queries Fusion task log + `~/.coywolf/state/coywolf-qa-state.json` (the latter via a small read-only HTTP endpoint exposed from the coywolf-qa runner OR via a Tailscale-served file).
- No write actions in the dashboard. Decisions about what to do (tune the planner, file an investigation task) flow back through the normal Lucid intake channel.

If Lucid is too heavy, fallback is a one-shot CLI:

```sh
python3 -m neumann.router.qa_stats --since 7d
```

prints all the aggregations as plain text. Cheap to build, lossy compared to a live page.

---

## Acceptance

1. A way to ask "which Type has the highest failure rate this week?" and get a number plus the worst N tasks.
2. A way to see the disagreement set (PASS pre-merge, FAIL post-deploy) — the cross-vendor signal.
3. A way to list normalized planner-bug messages with counts so the planner can be tuned against real failure modes.
4. No write surface. Read-only dashboard.

---

## Non-goals

- Real-time streaming. Daily-or-on-demand aggregation is fine.
- Cross-project rollups. One Lucid project at a time.
- Auto-tuning the Planner from the data. That's a separate spec — surface the data first; act on it after a few weeks of human-in-the-loop pattern matching.

---

## Open questions

- Where does the post-deploy state file live in production — file on Mac Mini disk, or echoed to a small JSON HTTP endpoint? File-on-disk is simplest; HTTP endpoint is friendlier to a Lucid sub-page that lives elsewhere. Defer to implementation.
- Should the dashboard also surface intake-funnel metrics (auto-filed Fix tasks vs. Brendan-approved → shipped vs. discarded)? Probably yes, but that crosses into Lucid intake panel scope; defer to that spec.

---

## Implementation order (when undeferred)

1. CLI aggregator: `python3 -m neumann.router.qa_stats` reading both data sources. Plain-text output. (~1 day.)
2. Lucid sub-page consuming the same aggregator over HTTP. (~1 day after the CLI exists.)
3. Tune (rolling window, snooze controls, drill-down to per-task verdict). (Open-ended.)

---

## References

- Parent spec: `qa-agent.md` § Phase 4 — Polish (item 3)
- Phase 2 verdict log: `coywolffuturist/neumann:neumann/router/fusion_watcher.py`
- Phase 3 state file: `coywolffuturist/coywolf:services/coywolf-qa/coywolf_qa.py` `StateStore`
- Memory: `project_qa_agent_in_review.md`, `project_swarm_vision.md`
