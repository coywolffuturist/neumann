"""Observability — structured logging & metrics collection.

Every routing decision, validation result, and error is emitted as a
structured event. Events can be collected in-memory, written to a file,
or forwarded to an external sink (OTLP, Loki, etc.).

Usage:
    from neumann import NeumannLogger

    logger = NeumannLogger(level="INFO")
    logger.route("code_block", "terminal", "CodeBlockRenderer", duration_ms=0.4)
    logger.validation("sha256:abc", valid=False, reason="forbidden pattern")
    logger.error("FormatterCrash", "DiffRenderer", "IndexError: list index out of range")
    logger.metrics()  # -> summary dict
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any


# ── Structured event model ───────────────────────────────────────────────────

@dataclass
class RouteEvent:
    timestamp: str
    input_hash: str
    classified_as: str
    context: str
    formatter_selected: str
    duration_ms: float
    validation: dict[str, Any] = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)


@dataclass
class ErrorEvent:
    timestamp: str
    error_type: str
    component: str
    message: str
    severity: str = "error"


# ── Logger ────────────────────────────────────────────────────────────────────

class NeumannLogger:
    """Structured logger for Neumann observability."""

    def __init__(
        self,
        level: str = "INFO",
        max_events: int = 10_000,
        sink: logging.Handler | None = None,
    ) -> None:
        self._level = getattr(logging, level.upper(), logging.INFO)
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._route_count = 0
        self._error_count = 0
        self._validation_pass = 0
        self._validation_fail = 0
        self._total_duration_ms = 0.0

        # Python stdlib logger for external sinks
        self._log = logging.getLogger("neumann")
        self._log.setLevel(self._level)
        if sink:
            self._log.addHandler(sink)
        elif not self._log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            self._log.addHandler(handler)

    # ── public ────────────────────────────────────────────────────────────────

    def route(
        self,
        classified_as: str,
        context: str,
        formatter: str,
        duration_ms: float,
        input_hash: str = "",
        validation: dict[str, Any] | None = None,
        trace: list[str] | None = None,
    ) -> None:
        self._route_count += 1
        self._total_duration_ms += duration_ms
        event = RouteEvent(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            input_hash=input_hash,
            classified_as=classified_as,
            context=context,
            formatter_selected=formatter,
            duration_ms=round(duration_ms, 3),
            validation=validation or {},
            trace=trace or [],
        )
        self._emit("route", asdict(event))

    def validation(
        self,
        input_hash: str,
        valid: bool,
        reason: str = "",
        severity: str = "",
    ) -> None:
        if valid:
            self._validation_pass += 1
        else:
            self._validation_fail += 1
        self._emit("validation", {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "input_hash": input_hash,
            "valid": valid,
            "reason": reason,
            "severity": severity,
        })

    def error(
        self,
        error_type: str,
        component: str,
        message: str,
        severity: str = "error",
    ) -> None:
        self._error_count += 1
        event = ErrorEvent(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            error_type=error_type,
            component=component,
            message=message,
            severity=severity,
        )
        self._emit("error", asdict(event))
        self._log.error("[%s] %s: %s", component, error_type, message)

    def metrics(self) -> dict[str, Any]:
        avg_ms = (
            round(self._total_duration_ms / self._route_count, 3)
            if self._route_count else 0.0
        )
        return {
            "routes_total": self._route_count,
            "errors_total": self._error_count,
            "validation_pass": self._validation_pass,
            "validation_fail": self._validation_fail,
            "avg_duration_ms": avg_ms,
            "event_buffer_size": len(self._events),
        }

    def events(self) -> list[dict[str, Any]]:
        """Return a copy of the event buffer."""
        return list(self._events)

    def export_json(self, indent: int = 2) -> str:
        """Export all events + metrics as a JSON string."""
        return json.dumps({
            "metrics": self.metrics(),
            "events": self.events(),
        }, indent=indent)

    # ── private ───────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        self._events.append({"type": event_type, **data})
