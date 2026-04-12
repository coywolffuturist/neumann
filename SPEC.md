# Neumann Spec v0.1
# Deterministic Symbolic Routing Kernel

## Overview

Neumann is the control layer between probabilistic LLM output and deterministic downstream systems.

## Core Data Types

```typescript
type TokenType =
  | 'code_block'
  | 'inline_code'
  | 'diff'
  | 'tool_call'
  | 'tool_result'
  | 'error'
  | 'markdown'
  | 'agent_state'
  | 'plain_text'
  | 'unknown';

type RenderContext =
  | 'terminal'
  | 'ide'
  | 'api_json'
  | 'web_html'
  | 'agent_passthrough';

type ValidationResult =
  | { valid: true }
  | { valid: false; reason: string; severity: 'warn' | 'error' | 'fatal' };

interface Token {
  type: TokenType;
  raw: string;
  metadata?: Record<string, unknown>;
}

interface RoutingDecision {
  formatter: string;
  context: RenderContext;
  priority: number;
  trace: string[];  // audit log of routing steps
}
```

## Module Contracts

### TokenClassifier
```typescript
interface TokenClassifier {
  classify(raw: string): Token;
  // Rules are loaded from rules.json — not hardcoded
  // Priority-ordered: first matching rule wins
}
```

### ContextResolver
```typescript
interface ContextResolver {
  resolve(env: Environment): RenderContext;
  // Pure function — no side effects
  // env includes: terminal capabilities, output target, agent mode
}
```

### FormatSelector
```typescript
interface FormatSelector {
  select(token: Token, context: RenderContext): Formatter;
  // Dispatch table — not nested IF-THEN
  // (TokenType × RenderContext) → Formatter
  // Missing entries → FallbackHandler
}
```

### SchemaValidator
```typescript
interface SchemaValidator {
  validate(output: string, schema: Schema): ValidationResult;
  // Deterministic — no LLM involvement
  // Called before every output emission
}
```

## Rule Format (rules.json)

```json
{
  "version": "1",
  "rules": [
    {
      "priority": 1,
      "pattern": "^```(\\w+)?\\n",
      "type": "code_block",
      "extract": { "language": "$1" }
    },
    {
      "priority": 2,
      "pattern": "^(@@|---|\\/\\/\\/ diff)",
      "type": "diff"
    },
    {
      "priority": 3,
      "pattern": "^\\{\"tool\":",
      "type": "tool_call"
    }
  ]
}
```

## Dispatch Table (dispatch.json)

```json
{
  "version": "1",
  "dispatch": [
    { "type": "code_block", "context": "terminal",   "formatter": "CodeBlockRenderer", "options": { "highlight": true } },
    { "type": "code_block", "context": "api_json",   "formatter": "CodeBlockRenderer", "options": { "highlight": false } },
    { "type": "diff",       "context": "terminal",   "formatter": "DiffRenderer",      "options": { "style": "unified" } },
    { "type": "tool_call",  "context": "*",           "formatter": "ToolCallRenderer"  },
    { "type": "error",      "context": "*",           "formatter": "ErrorRenderer"     },
    { "type": "*",          "context": "*",           "formatter": "FallbackHandler"   }
  ]
}
```

## Observability

Every routing decision emits a structured event:

```json
{
  "timestamp": "2026-04-12T16:00:00Z",
  "input_hash": "sha256:abc123",
  "classified_as": "code_block",
  "context": "terminal",
  "formatter_selected": "CodeBlockRenderer",
  "validation": { "valid": true },
  "duration_ms": 0.4
}
```

## Extension Points

1. **Add a new token type**: add a rule to `rules.json`
2. **Add a new formatter**: implement the `Formatter` interface, register in `dispatch.json`
3. **Add a new context**: extend `RenderContext` type, add rows to dispatch table
4. **Custom validation**: implement `SchemaValidator` interface with your schema
