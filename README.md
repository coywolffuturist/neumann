# Neumann

> A deterministic symbolic routing kernel for AI agent systems.

Conceieved and named by BJ (@azzabazazz, the creator of @coywolffuturist) after John von Neumann — whose architecture separated the control unit from computation, enabling programmable, reliable machines. Neumann does the same for AI agents: the LLM generates, Neumann controls.

## The Problem

Claude Code's `print.ts` is a 3,167-line monolithic IF-THEN kernel with 486 branch points and 12 levels of nesting. It works. But it's untestable, unmaintainable, and opaque. Leading engineers have noted it should be 10-12 discrete modules.

The same problem exists in every agent system: a probabilistic LLM at the center, surrounded by ad-hoc routing logic that grows into an unmaintainable tangle. Neumann is the clean alternative.

## What Neumann Is

A modular, deterministic symbolic layer that sits between LLM output and downstream systems. It classifies, validates, routes, and renders — reliably, testably, observably.

**LLM generates. Neumann controls.**

## Architecture

```
Input (raw LLM stream / agent output)
        ↓
  TokenClassifier       — what type is this? (code, tool_call, diff, error, text...)
        ↓
  ContextResolver       — what is the current environment? (terminal, IDE, API, agent)
        ↓
  FormatSelector        — pure dispatch: (type, context) → formatter
        ↓
  [Formatter Pool]
  ├── CodeBlockRenderer     — syntax, language detection, line numbers
  ├── DiffRenderer          — unified diff, side-by-side, inline
  ├── ToolCallRenderer      — tool invocations + results
  ├── ErrorRenderer         — stack traces, error types, suggested fixes
  ├── MarkdownRenderer      — context-aware (strip for terminal, keep for web)
  ├── AgentStateRenderer    — progress, subagent status, thinking blocks
  └── StreamingController   — buffering, flush decisions, partial renders
        ↓
  SchemaValidator       — deterministic pass/fail gate before output
        ↓
  FallbackHandler       — graceful degradation when no formatter matches
        ↓
  Output
```

## Design Principles

### 1. Pure Functions Throughout
Every module is a pure function. Same input → same output. No hidden state. No side effects. Fully testable in isolation.

### 2. Dispatch Tables, Not Nested IF-THEN
The `FormatSelector` is a dispatch table:
```
(type, context) → formatter
```
Adding a new output type = adding one row to the table. No branching logic to update, no nesting to navigate.

### 3. Rules Are Data
The `TokenClassifier` uses a priority-ordered rule set defined in JSON/YAML — not hardcoded. Rules are editable without redeployment. The rule engine is a small, stable interpreter.

### 4. The Validator Is the Guarantee
`SchemaValidator` is the symbolic gate. Before anything leaves the system, it passes a deterministic schema check. This is where you catch:
- Hallucinated tool calls
- Malformed diffs
- Broken JSON
- Contract violations

### 5. Full Observability
Every module emits structured logs. Every routing decision is traceable. You can replay any input and see exactly why it was handled the way it was.

## The 12 Modules

| # | Module | Responsibility |
|---|--------|----------------|
| 1 | `TokenClassifier` | Classify incoming chunks by type |
| 2 | `ContextResolver` | Determine rendering/routing context |
| 3 | `FormatSelector` | Dispatch to correct formatter |
| 4 | `CodeBlockRenderer` | Code formatting, syntax, line numbers |
| 5 | `DiffRenderer` | Unified diff, side-by-side, inline |
| 6 | `ToolCallRenderer` | Tool invocations and results |
| 7 | `ErrorRenderer` | Stack traces, error classification |
| 8 | `MarkdownRenderer` | Context-aware markdown processing |
| 9 | `AgentStateRenderer` | Progress indicators, subagent status |
| 10 | `StreamingController` | Buffer management, flush decisions |
| 11 | `SchemaValidator` | Output contract enforcement |
| 12 | `FallbackHandler` | Graceful degradation |

## Application to Agent Systems (Mitosis)

Neumann's architecture maps directly onto agent orchestration:

| Neumann | Mitosis Equivalent |
|---------|-------------------|
| `TokenClassifier` | `MessageClassifier` — what kind of agent output is this? |
| `ContextResolver` | `AgentContextResolver` — which agent, what permissions, what state? |
| `FormatSelector` | `RouteSelector` — which downstream system handles this? |
| `SchemaValidator` | `ContractValidator` — does this output satisfy the interface contract? |
| `FallbackHandler` | `EscalationHandler` — when no route matches, escalate to human |

The symbolic layer *is* the reliability guarantee. The LLM generates; Neumann validates and routes.

## Relationship to Neurosymbolic AI

Gary Marcus' analysis of Claude Code (April 2026) identified `print.ts` as evidence that Anthropic, when reliability mattered, reached for classical symbolic AI — not more LLM. Neumann makes this architectural choice explicit and principled:

- **LLM** = probabilistic generation, language understanding, creative reasoning
- **Neumann** = deterministic classification, routing, validation, rendering

Neither replaces the other. Together they are more reliable than either alone.

## Status

Early spec. Contributions welcome.

## License

MIT
