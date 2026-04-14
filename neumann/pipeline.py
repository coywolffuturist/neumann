"""NeumannPipeline — orchestrates the full classification → routing → validation → render → execute flow.

Features:
- Structured observability (logger)
- Error recovery (formatter crash → fallback)
- Hot-reload rules (classifier/selector/dispatch)
- Tool execution (bash, read_file, write_file, edit_file, grep)

Usage:
    pipeline = NeumannPipeline()
    result = pipeline.process(raw_text, env={"is_api": True})
    print(result.rendered)

    # Execute a tool call
    result = pipeline.execute_tool_call('{"tool": "bash", "input": {"command": "ls"}}')
    print(result.rendered)
"""
from __future__ import annotations

import time
import hashlib
import json
import traceback
from dataclasses import dataclass
from typing import Any

from .classifier import TokenClassifier
from .context import ContextResolver
from .selector import FormatSelector
from .validator import SchemaValidator
from .registry import get_formatter
from .logger import NeumannLogger
from .tools.registry import execute_tool, register_defaults
from .types import Token, TokenType, RenderContext, RoutingDecision, ValidationResult


@dataclass
class PipelineResult:
    token: Token
    context: RenderContext
    decision: RoutingDecision
    validation: ValidationResult
    rendered: str
    duration_ms: float
    input_hash: str
    recovered: bool = False  # True if fallback was used after error


class NeumannPipeline:
    def __init__(
        self,
        classifier: TokenClassifier | None = None,
        resolver: ContextResolver | None = None,
        selector: FormatSelector | None = None,
        validator: SchemaValidator | None = None,
        output_schema: dict[str, Any] | None = None,
        logger: NeumannLogger | None = None,
    ) -> None:
        self.classifier = classifier or TokenClassifier()
        self.resolver = resolver or ContextResolver()
        self.selector = selector or FormatSelector()
        self.validator = validator or SchemaValidator()
        self.output_schema = output_schema or {}
        self.logger = logger or NeumannLogger(level="INFO")

    def process(self, raw: str, env: dict[str, Any] | None = None) -> PipelineResult:
        """Run raw text through the full Neumann pipeline.

        Error recovery: if the formatter crashes, we fall back to FallbackHandler
        and continue — the pipeline never crashes.
        """
        start = time.perf_counter()
        input_hash = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:12]

        token = self.classifier.classify(raw)
        context = self.resolver.resolve(env)
        decision = self.selector.select(token, context)

        # Render with error recovery
        recovered = False
        try:
            formatter = get_formatter(decision.formatter)
            rendered = formatter.render(token, context)
        except Exception as e:
            # Error recovery: fall back to FallbackHandler
            self.logger.error(
                error_type=type(e).__name__,
                component=decision.formatter,
                message=str(e) or traceback.format_exc(),
                severity="error",
            )
            decision.trace.append(f"ERROR in {decision.formatter}: {e} — falling back")
            formatter = get_formatter("FallbackHandler")
            rendered = formatter.render(token, context)
            recovered = True
            decision = RoutingDecision(
                formatter="FallbackHandler",
                context=context,
                priority=99,
                trace=decision.trace,
            )

        # Validate rendered output
        validation = (
            self.validator.validate(rendered, self.output_schema)
            if self.output_schema
            else ValidationResult(valid=True)
        )

        duration_ms = (time.perf_counter() - start) * 1000

        # Observability
        self.logger.route(
            classified_as=token.type.value,
            context=context.value,
            formatter=decision.formatter,
            duration_ms=duration_ms,
            input_hash=input_hash,
            validation={"valid": validation.valid, "reason": validation.reason},
            trace=decision.trace,
        )
        if not validation.valid:
            self.logger.validation(
                input_hash=input_hash,
                valid=False,
                reason=validation.reason,
                severity=validation.severity,
            )

        return PipelineResult(
            token=token,
            context=context,
            decision=decision,
            validation=validation,
            rendered=rendered,
            duration_ms=round(duration_ms, 3),
            input_hash=input_hash,
            recovered=recovered,
        )

    # ── hot-reload ────────────────────────────────────────────────────────────

    def reload_rules(self) -> None:
        """Reload classification rules and dispatch table from disk."""
        self.classifier = TokenClassifier()
        self.selector = FormatSelector()
        self.logger._log.info("Neumann rules reloaded from disk")

    def reload_formatters(self) -> None:
        """Reload formatter registry (for development / plugin systems)."""
        from .registry import _REGISTRY
        _REGISTRY.clear()
        from .formatters.code import CodeBlockRenderer
        from .formatters.diff import DiffRenderer
        from .formatters.tool_call import ToolCallRenderer
        from .formatters.error import ErrorRenderer
        from .formatters.markdown import MarkdownRenderer
        from .formatters.agent_state import AgentStateRenderer
        from .formatters.fallback import PlainTextRenderer, FallbackHandler
        _REGISTRY.update({
            "CodeBlockRenderer":  CodeBlockRenderer(),
            "DiffRenderer":       DiffRenderer(),
            "ToolCallRenderer":   ToolCallRenderer(),
            "ErrorRenderer":      ErrorRenderer(),
            "MarkdownRenderer":   MarkdownRenderer(),
            "AgentStateRenderer": AgentStateRenderer(),
            "PlainTextRenderer":  PlainTextRenderer(),
            "FallbackHandler":    FallbackHandler(),
        })
        self.logger._log.info("Neumann formatters reloaded")

    # ── tool execution ────────────────────────────────────────────────────────

    def execute_tool_call(self, raw: str, env: dict[str, Any] | None = None) -> PipelineResult:
        """Parse a JSON tool call, execute the tool, and return a PipelineResult.

        Input: '{"tool": "bash", "input": {"command": "ls -la"}}'
        """
        start = time.perf_counter()
        input_hash = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:12]

        # Ensure tools are registered
        from .tools.registry import _REGISTRY as TOOL_REGISTRY, register_defaults
        if not TOOL_REGISTRY:
            register_defaults()

        # Parse the tool call
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            token = Token(type=TokenType.ERROR, raw=raw, metadata={"reason": "invalid JSON"})
            return self._error_result(token, "Invalid JSON in tool call", input_hash)

        tool_name = data.get("tool")
        tool_input = data.get("input", {})

        if not tool_name:
            token = Token(type=TokenType.ERROR, raw=raw, metadata={"reason": "no tool name"})
            return self._error_result(token, "No 'tool' key in tool call", input_hash)

        # Execute
        result = execute_tool(tool_name, **tool_input)

        # Build token from result
        token_type = TokenType.TOOL_RESULT if result.success else TokenType.ERROR
        token = Token(
            type=token_type,
            raw=json.dumps(result.to_dict(), indent=2),
            metadata={"tool_name": tool_name, "success": result.success},
        )

        # Render
        context = self.resolver.resolve(env)
        formatter = get_formatter("ToolCallRenderer" if result.success else "ErrorRenderer")
        rendered = formatter.render(token, context)

        duration_ms = (time.perf_counter() - start) * 1000

        self.logger.route(
            classified_as=f"tool:{tool_name}",
            context=context.value,
            formatter="ToolCallRenderer",
            duration_ms=duration_ms,
            input_hash=input_hash,
            validation={"valid": result.success},
        )

        return PipelineResult(
            token=token,
            context=context,
            decision=RoutingDecision(
                formatter="ToolCallRenderer",
                context=context,
                priority=1,
                trace=[f"executed tool: {tool_name}"],
            ),
            validation=ValidationResult(valid=result.success),
            rendered=rendered,
            duration_ms=round(duration_ms, 3),
            input_hash=input_hash,
        )

    def _error_result(
        self, token: Token, reason: str, input_hash: str,
    ) -> PipelineResult:
        context = self.resolver.resolve({})
        formatter = get_formatter("ErrorRenderer")
        rendered = formatter.render(token, context)
        return PipelineResult(
            token=token,
            context=context,
            decision=RoutingDecision(
                formatter="ErrorRenderer", context=context, priority=1,
                trace=[reason],
            ),
            validation=ValidationResult(valid=False, reason=reason),
            rendered=rendered,
            duration_ms=0.0,
            input_hash=input_hash,
        )
