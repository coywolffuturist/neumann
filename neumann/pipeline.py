"""NeumannPipeline — orchestrates the full classification → routing → validation → render flow.

Usage:
    pipeline = NeumannPipeline()
    result = pipeline.process(raw_text, env={"is_api": True})
    print(result.rendered)
"""
from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass
from typing import Any

from .classifier import TokenClassifier
from .context import ContextResolver
from .selector import FormatSelector
from .validator import SchemaValidator
from .registry import get_formatter
from .types import Token, RenderContext, RoutingDecision, ValidationResult


@dataclass
class PipelineResult:
    token: Token
    context: RenderContext
    decision: RoutingDecision
    validation: ValidationResult
    rendered: str
    duration_ms: float
    input_hash: str


class NeumannPipeline:
    def __init__(
        self,
        classifier: TokenClassifier | None = None,
        resolver: ContextResolver | None = None,
        selector: FormatSelector | None = None,
        validator: SchemaValidator | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        self.classifier = classifier or TokenClassifier()
        self.resolver = resolver or ContextResolver()
        self.selector = selector or FormatSelector()
        self.validator = validator or SchemaValidator()
        self.output_schema = output_schema or {}

    def process(self, raw: str, env: dict[str, Any] | None = None) -> PipelineResult:
        """Run raw text through the full Neumann pipeline."""
        start = time.perf_counter()
        input_hash = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:12]

        token = self.classifier.classify(raw)
        context = self.resolver.resolve(env)
        decision = self.selector.select(token, context)

        # Render
        formatter = get_formatter(decision.formatter)
        rendered = formatter.render(token, context)

        # Validate rendered output
        validation = (
            self.validator.validate(rendered, self.output_schema)
            if self.output_schema
            else ValidationResult(valid=True)
        )

        duration_ms = (time.perf_counter() - start) * 1000

        return PipelineResult(
            token=token,
            context=context,
            decision=decision,
            validation=validation,
            rendered=rendered,
            duration_ms=round(duration_ms, 3),
            input_hash=input_hash,
        )
