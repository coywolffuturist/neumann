# Graph Report - neumann  (2026-05-13)

## Corpus Check
- 71 files · ~39,573 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1004 nodes · 1939 edges · 62 communities (51 shown, 11 thin omitted)
- Extraction: 74% EXTRACTED · 26% INFERRED · 0% AMBIGUOUS · INFERRED: 501 edges (avg confidence: 0.64)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1cf79c6b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]

## God Nodes (most connected - your core abstractions)
1. `RouterPipeline` - 39 edges
2. `parse_qa_test()` - 36 edges
3. `QAExecutor` - 35 edges
4. `WatcherState` - 34 edges
5. `PlannedTask` - 32 edges
6. `ConfirmedIntent` - 31 edges
7. `RetryPolicy` - 30 edges
8. `QATask` - 29 edges
9. `Decomposer` - 27 edges
10. `PipelineResult` - 25 edges

## Surprising Connections (you probably didn't know these)
- `test_fallback_for_unknown()` --calls--> `Token`  [INFERRED]
  tests/test_selector.py → neumann/types.py
- `classifier()` --calls--> `ShapeClassifier`  [INFERRED]
  tests/router/test_shape_classifier.py → neumann/router/shape_classifier.py
- `test_flush_remaining()` --calls--> `AsyncStreamingController`  [INFERRED]
  tests/test_streaming_async.py → neumann/streaming_async.py
- `test_flush_empty()` --calls--> `AsyncStreamingController`  [INFERRED]
  tests/test_streaming_async.py → neumann/streaming_async.py
- `test_stats()` --calls--> `AsyncStreamingController`  [INFERRED]
  tests/test_streaming_async.py → neumann/streaming_async.py

## Communities (62 total, 11 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.08
Nodes (41): FusionTask, The minimum Fusion task fields the watcher needs.      Mapped from Fusion's daem, WatcherStats, WhatsAppNotifier, Watcher state for the pre-merge QA gate.  Fusion's task object has no ``extra``, JSON-on-disk retry-count + last-verdict log. Atomic writes; corrupt-tolerant., Remove the record (e.g. after a pause is manually unpaused by Brendan)., WatcherRecord (+33 more)

### Community 1 - "Community 1"
Cohesion: 0.14
Nodes (39): _browser_tool_for_type(), _enforce_no_banned_tools(), _extract_section(), _model_for_tier(), _parse_expected_failures(), _parse_fields(), parse_qa_test(), _parse_steps() (+31 more)

### Community 2 - "Community 2"
Cohesion: 0.1
Nodes (34): _coerce_int(), TaskDecomposer — split oversized ``PlannedTask``s into children + integration., Replace ``task`` with N children + 1 integration task.          Children are 1-t, Best-effort int coercion. The Planner's ``extra`` dict comes from     LLM JSON a, Splits oversized PlannedTasks into children + an integration task.      ``rules_, TaskDecomposer, _make_plan(), _make_task() (+26 more)

### Community 3 - "Community 3"
Cohesion: 0.11
Nodes (32): _collect_prose(), _count_distinct_files(), _count_distinct_outputs(), Decomposer, _estimate_lines(), _infer_seams(), _intent_id(), _load_rules() (+24 more)

### Community 4 - "Community 4"
Cohesion: 0.1
Nodes (31): load_policy(), Pure-function retry policy. Default: 2 retries (3 total attempts)., Return the action to take given a verdict and the 1-indexed attempt number., Load retry policy from env override > Fusion config > default.      Resolution o, RetryPolicy, Tests for the QA retry / escalation policy.  Spec: ``docs/specs/qa-agent.md`` §, ``max_qa_retries: 0`` is a valid setting (no retries). Don't truthy-check., At max_retries=2, attempt=3 means we've used 2 retries — escalate. (+23 more)

### Community 5 - "Community 5"
Cohesion: 0.11
Nodes (16): _build_default_watcher(), ClawdbotWhatsAppNotifier, DryRunFusionClient, DryRunWatcherState, _format_failure_context(), _format_pause_reason(), _format_whatsapp_message(), FusionWatcher (+8 more)

### Community 6 - "Community 6"
Cohesion: 0.12
Nodes (28): _ctx(), _ctx_col(), Tests for PersonaSelector — (TaskType, RoutingContext) → Persona., If selected persona isn't in available_personas, dispatch downgrades., Pre-merge QA gate: any task entering In Review goes to qa persona., Initial dispatch (no column) must not route to qa just because the rule exists., Columns other than in-review do not trigger the QA override., test_backend_routes_to_backend_engineer() (+20 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (21): Emergency flush when buffer exceeds max_buffer., Feed a raw chunk from the LLM stream. Yields complete PipelineResults., Flush any remaining buffered content. Call after the stream ends., Try to extract and emit complete tokens from the buffer., StreamingController, collect(), Tests for StreamingController., Code fence split mid-open — controller must buffer until closing fence. (+13 more)

### Community 8 - "Community 8"
Cohesion: 0.07
Nodes (27): Acceptance criteria, Anti-patterns, Architecture, Browser-interaction policy (hardcoded), code:block1 (Task lifecycle in Fusion:), code:markdown (## QA Test), code:json ({), code:python (# Pseudo-code) (+19 more)

### Community 9 - "Community 9"
Cohesion: 0.23
Nodes (24): QAExecutor, QATask, Input to the QA executor — a Fusion task entering In Review., Runs one QA Test attempt. Stateless — instantiate per attempt., _good_prompt(), Tests for QAExecutor — the per-attempt orchestration layer., Pre-merge executor should not run post-deploy-only tests — that's Coywolf's job., Malformed step entries don't crash the executor. (+16 more)

### Community 10 - "Community 10"
Cohesion: 0.17
Nodes (22): Tests for all formatters., render(), test_agent_state_terminal(), test_agent_state_web(), test_code_api_is_raw(), test_code_no_fences_in_api(), test_code_terminal_has_border(), test_code_web_has_pre_tag() (+14 more)

### Community 11 - "Community 11"
Cohesion: 0.16
Nodes (19): MockInterviewer, Returns a canned ConfirmedIntent for tests + offline development.      Two regis, Pure function. Schema-validates a ConfirmedIntent.      Returns ``valid=True`` o, validate_intent(), Tests for the Interviewer module — interview loop + intent validation., test_empty_confirmed_intent_fails(), test_malformed_target_repo_fails(), test_missing_success_criteria_fails() (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.14
Nodes (16): ContextResolver, Resolve the current RenderContext from an environment dict.          Priority or, NeumannPipeline, PipelineResult, Run raw text through the full Neumann pipeline., get_formatter(), FormatSelector, _load() (+8 more)

### Community 13 - "Community 13"
Cohesion: 0.09
Nodes (21): code:typescript (type TokenType =), code:typescript (interface TokenClassifier {), code:typescript (interface ContextResolver {), code:typescript (interface FormatSelector {), code:typescript (interface SchemaValidator {), code:json ({), code:json ({), code:json ({) (+13 more)

### Community 14 - "Community 14"
Cohesion: 0.15
Nodes (10): ContextResolver — determines the rendering/routing context from the environment., NeumannPipeline — orchestrates the full classification → routing → validation →, FormatSelector — dispatches (TokenType, RenderContext) → formatter name.  Dispat, AsyncStreamingController — async version of StreamingController.  Identical logi, StreamingController — buffers a chunked LLM stream and flushes complete tokens., ValidationResult, SchemaValidator — deterministic output gate before emission.  Validates that out, Validate output against a schema dict.          Supported schema keys:         - (+2 more)

### Community 15 - "Community 15"
Cohesion: 0.16
Nodes (14): AsyncStreamingController, Feed a raw chunk. Yields complete PipelineResults asynchronously., Flush remaining buffer after the stream ends., collect_async(), Tests for AsyncStreamingController., test_code_block_split(), test_context_propagated(), test_flush_empty() (+6 more)

### Community 16 - "Community 16"
Cohesion: 0.14
Nodes (14): Interviewer, InterviewQuestion, load_questions(), LucidInterviewer, Interviewer — clarifying Q&A loop that produces a ConfirmedIntent.  The Intervie, Anything that runs an interview loop and returns a ConfirmedIntent., Interviewer that runs in a Slack thread.      The caller wires ``send_message``, Interviewer for Brendan's personal Lucid dashboard chat panel.      Lucid render (+6 more)

### Community 17 - "Community 17"
Cohesion: 0.1
Nodes (20): Branch state, code:block1 ($ printf 'pakt-world/paktsuite-v2\nGET /healthz returns 200 ), code:block2 (neumann/router/), code:sh (cd /tmp/neumann), code:sh ($ python3 -m neumann.router.cli classify-shape "Fix the typo), code:python (import json, sqlite3, secrets), code:python (subprocess.run([), code:sh (cd /tmp/neumann) (+12 more)

### Community 18 - "Community 18"
Cohesion: 0.1
Nodes (20): Adding a new persona, Adding a new task type, CLI, code:block1 (User prompt), code:block2 (neumann/router/), code:sh (# Classify a prompt's shape), code:python (from queue import Queue), code:json ({) (+12 more)

### Community 19 - "Community 19"
Cohesion: 0.16
Nodes (19): Tests for ChatInterviewer and the Slack/Lucid/Web concrete subclasses.  Each sub, 🐺 reaction OR 'ship it' both work as approval., Frontend posts literal 'APPROVE' on button click — must register as approval., Returns (send, wait, sent_messages) — a queue-backed mock transport., 2touch is team-facing — drop the wolf and use a buttoned-up header., The chat approval lexicon should accept emojis and Coywolf idioms., Empty queue → wait raises TimeoutError → interview raises InterviewIncomplete., _scripted_transport() (+11 more)

### Community 20 - "Community 20"
Cohesion: 0.15
Nodes (13): invocation(), Run a raw user prompt through the full intake pipeline.          Stage order:, Route a single PlannedTask, skipping shape classification + planning., RouterPipeline, pipeline(), End-to-end tests for RouterPipeline.  Pipes a raw user prompt through shape → (p, Verification needs qa-engineer; if QA is unavailable, fallback handler fills in., test_input_hash_and_duration_are_populated() (+5 more)

### Community 21 - "Community 21"
Cohesion: 0.15
Nodes (11): If decision is the fallback sentinel, replace with a real persona.          Othe, RoutingFallback, _load(), _matches(), PersonaSelector, PersonaSelector — pure dispatch from (TaskType, RoutingContext) → Persona.  Disp, Return the best matching ``PersonaDecision``.          ``task`` and ``type_match, PersonaRegistry (+3 more)

### Community 22 - "Community 22"
Cohesion: 0.16
Nodes (17): Tests for TaskTypeClassifier — structured PlannedTask → TaskType., A task that matches no specific rule should hit catch-all unknown., A task that says 'verify the typo fix' should route to QA, not engineer., A .tsx file is a far stronger signal than prose. File path wins., test_bugfix_keywords(), test_documentation_keywords(), test_finance_keywords(), test_marketing_keywords() (+9 more)

### Community 23 - "Community 23"
Cohesion: 0.17
Nodes (12): DryRunNotifier, Logs notifications instead of sending. WhatsApp stays quiet., ClaudeCliReviewer, _parse_steps(), _planner_bug_result(), QAResult, QAStepResult, Pre-merge QA executor — runs a single QA Test against a worktree.  Orchestration (+4 more)

### Community 24 - "Community 24"
Cohesion: 0.13
Nodes (13): Protocol, load_planner_spec(), MockPlanner, _MockPlannerEntry, Planner, Planner protocol — the contract between an LLM and the Neumann router.  The rout, Anything that turns a mission prompt into a structured Plan., Load the Planner persona JSON (soul + instructionsText) for prompt-building. (+5 more)

### Community 25 - "Community 25"
Cohesion: 0.11
Nodes (17): 1. Pure Functions Throughout, 2. Dispatch Tables, Not Nested IF-THEN, 3. Rules Are Data, 4. The Validator Is the Guarantee, 5. Full Observability, Application to Agent Systems (Mitosis), Architecture, code:block1 (Input (raw LLM stream / agent output)) (+9 more)

### Community 26 - "Community 26"
Cohesion: 0.18
Nodes (9): ChatInterviewer, _failure_to_field(), _format_question(), InterviewIncomplete, Raised by an Interviewer when it cannot produce a valid ConfirmedIntent., Surface-agnostic chat-based interview.      Concrete subclasses (``SlackIntervie, Wrap the question text with the surface header.          Default: prepend ``[rou, Return True if response indicates approval. Subclasses extend. (+1 more)

### Community 27 - "Community 27"
Cohesion: 0.12
Nodes (16): Acceptance criteria, Anti-patterns, Architecture, code:block1 (Prompt), code:python (class Decomposer:), code:python (def process(self, prompt: str, env: dict | None = None) -> P), Context, Goal (+8 more)

### Community 28 - "Community 28"
Cohesion: 0.19
Nodes (11): PipelineResult, _load(), ShapeClassifier — does this prompt describe a single task or a multi-task missio, Classify a raw user prompt as ``single-task`` or ``mission``.          Rules are, ShapeClassifier, Core types for the Neumann router.  These are pure data classes. No logic, no I/, Top-level shape of an incoming user prompt., Audit log of one routing decision — replayable, observable. (+3 more)

### Community 29 - "Community 29"
Cohesion: 0.16
Nodes (9): Formatter, CodeBlockRenderer, _highlight_terminal(), CodeBlockRenderer — renders code blocks and inline code.  Terminal: ANSI color s, FallbackHandler, PlainTextRenderer, PlainTextRenderer and FallbackHandler., Last resort — returns raw input with a warning comment in terminal mode. (+1 more)

### Community 30 - "Community 30"
Cohesion: 0.18
Nodes (7): RoutingFallback — handles fallback persona selection when dispatch fails.  Two b, neumann.router — Deterministic persona-routing kernel for AI agent systems.  Sit, RouterPipeline — orchestrates the universal intake pipeline.  Brendan's column-m, get_persona(), PersonaRegistry — persona id → metadata lookup.  Loads the 9 Fusion preset perso, Add or overwrite a persona at runtime., RoutingValidator — pre-flight gate before a routing decision is acted upon.  Det

### Community 31 - "Community 31"
Cohesion: 0.14
Nodes (6): _Invocation, query(), telemetry — append-only JSONL logger for harness invocations.  Every harness (br, Context manager that records a single harness invocation., Iterate records for a harness, optionally filtered., ValueError

### Community 33 - "Community 33"
Cohesion: 0.14
Nodes (13): code:markdown (## QA Test), code:markdown (## QA Test), code:json ({), Communication Style, Field semantics, Format, Hard prohibitions, Operating Principles (+5 more)

### Community 34 - "Community 34"
Cohesion: 0.14
Nodes (13): Acceptance, Aggregations, code:sh (python3 -m neumann.router.qa_stats --since 7d), Goal, Implementation order (when undeferred), Inputs, Non-goals, Open questions (+5 more)

### Community 35 - "Community 35"
Cohesion: 0.22
Nodes (11): Enum, TokenType, Retry / escalation policy for the QA gate.  Pure functions. No I/O. Honored by b, Decision for what to do after a single QA attempt., RetryAction, QATestType, ReviewerTier, Return ``(task_type, matched_rule_metadata)``.          The metadata dict carrie (+3 more)

### Community 36 - "Community 36"
Cohesion: 0.28
Nodes (3): HttpFusionClient, Default Fusion daemon client. Real wire format per     ``reference_fusion_daemon, Return Fusion project IDs. Empty list on failure (caller still         falls bac

### Community 37 - "Community 37"
Cohesion: 0.15
Nodes (12): Active branches, AGENTS.md — Neumann, code:block1 (User prompt), Commit hygiene, Context Navigation (auto-generated), Cross-references, Layout, Memory (+4 more)

### Community 38 - "Community 38"
Cohesion: 0.21
Nodes (11): CLIInterviewer, Runs an interview via stdin/stdout. For local use.      Real Slack/web interview, Helpers that mimic input()/print() for a scripted CLI run., Wrong-org repo answer triggers re-ask; valid retry completes the interview., Persistent bad answers exhaust the loop and raise InterviewIncomplete., User can refine the confirmed_intent at the approval gate., _scripted_io(), test_cli_interviewer_happy_path() (+3 more)

### Community 39 - "Community 39"
Cohesion: 0.17
Nodes (6): classifier(), Tests for ShapeClassifier — mission vs single-task., Two sentences alone aren't enough to escalate — we want >= 3., No prompt — even gibberish — should fall through cleanly to single-task., test_catch_all_always_resolves(), test_two_short_sentences_is_single_task()

### Community 40 - "Community 40"
Cohesion: 0.24
Nodes (5): ABC, Formatter, Base Formatter interface. All formatters implement this., ToolCallRenderer — renders tool invocations and results.  Parses JSON envelope:, ToolCallRenderer

### Community 41 - "Community 41"
Cohesion: 0.18
Nodes (5): FusionClient, Abstraction over Fusion's daemon API. Tests mock this; production     uses ``Htt, QAReviewer, The LLM-backed reviewer. Production impl shells out to pi-claude-cli;     tests, Return the raw JSON string produced by the reviewer LLM.          The string mus

### Community 43 - "Community 43"
Cohesion: 0.31
Nodes (6): _extract(), _load_rules(), TokenClassifier — classifies raw text chunks into TokenTypes.  Rules are loaded, Classify a raw text chunk. Returns a Token with type and metadata., TokenClassifier, Token

### Community 44 - "Community 44"
Cohesion: 0.22
Nodes (8): Anti-Patterns, Browser Interaction Rules (hardcoded), code:json ({), Execution Protocol, Operating Posture: Independent Skepticism, Output Format, QA Reviewer — System Prompt, Tool Boundaries

### Community 45 - "Community 45"
Cohesion: 0.39
Nodes (3): _inline_html(), MarkdownRenderer, MarkdownRenderer — context-aware markdown processing.  Terminal: strip markdown

### Community 47 - "Community 47"
Cohesion: 0.32
Nodes (5): ContextResolver, _infer_project_type(), ContextResolver — derives a RoutingContext from environment + task signals.  Pur, Environmental context that influences persona selection.      Set by the Context, RoutingContext

### Community 48 - "Community 48"
Cohesion: 0.43
Nodes (3): ErrorRenderer, _parse_error(), ErrorRenderer — renders Python/JS errors and tracebacks.  Terminal: ANSI red hea

### Community 49 - "Community 49"
Cohesion: 0.43
Nodes (3): AgentStateRenderer, _bar(), AgentStateRenderer — renders agent progress / subagent status blocks.  Parses: {

### Community 50 - "Community 50"
Cohesion: 0.43
Nodes (6): main(), _print_result(), fnr — the Neumann router CLI.  Two modes:      fnr task "<prompt>"            —, Convert enums + tuples for JSON serialization., _scrub(), _serialize_trace()

### Community 52 - "Community 52"
Cohesion: 0.4
Nodes (4): _load(), TaskTypeClassifier — classifies a structured PlannedTask into a TaskType.  Rules, TaskTypeClassifier, classifier()

### Community 53 - "Community 53"
Cohesion: 0.33
Nodes (5): End-to-end tests for RouterPipeline with the Interviewer wired in., The Interviewer's ConfirmedIntent flows into the Planner and onto the Plan., When no Interviewer is wired, pipeline behaves like v1: prompt → plan → route., test_pipeline_with_interviewer_passes_intent_to_planner(), test_pipeline_without_interviewer_v1_behavior()

## Knowledge Gaps
- **307 isolated node(s):** `SchemaValidator — deterministic output gate before emission.  Validates that out`, `Validate output against a schema dict.          Supported schema keys:         -`, `AsyncStreamingController — async version of StreamingController.  Identical logi`, `Feed a raw chunk. Yields complete PipelineResults asynchronously.`, `Flush remaining buffer after the stream ends.` (+302 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PipelineResult` connect `Community 28` to `Community 2`, `Community 3`, `Community 7`, `Community 11`, `Community 47`, `Community 15`, `Community 16`, `Community 20`, `Community 21`, `Community 52`, `Community 22`, `Community 24`, `Community 30`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Why does `RenderContext` connect `Community 12` to `Community 35`, `Community 7`, `Community 40`, `Community 45`, `Community 14`, `Community 48`, `Community 49`, `Community 51`, `Community 29`?**
  _High betweenness centrality (0.087) - this node is a cross-community bridge._
- **Why does `StreamingController` connect `Community 7` to `Community 12`, `Community 28`, `Community 14`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Are the 31 inferred relationships involving `RouterPipeline` (e.g. with `ContextResolver` and `Decomposer`) actually correct?**
  _`RouterPipeline` has 31 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `parse_qa_test()` (e.g. with `test_happy_path_returns_fully_populated_qatest()` and `test_pre_merge_only_skips_post_deploy_model_requirement()`) actually correct?**
  _`parse_qa_test()` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `QAExecutor` (e.g. with `FusionTask` and `FusionClient`) actually correct?**
  _`QAExecutor` has 30 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `WatcherState` (e.g. with `FusionTask` and `FusionClient`) actually correct?**
  _`WatcherState` has 24 INFERRED edges - model-reasoned connections that need verification._