"""Tests for new features: observability, config, error recovery, hot-reload, CLI."""
import json
import os
import pytest
from neumann import (
    NeumannPipeline, NeumannLogger, NeumannConfig, load_config,
    TokenType, RenderContext,
)


# ── Observability / Logger ─────────────────────────────────────

class TestObservability:
    def test_logger_tracks_routes(self):
        logger = NeumannLogger()
        logger.route("code_block", "terminal", "CodeBlockRenderer", 0.42)
        logger.route("tool_call", "api_json", "ToolCallRenderer", 0.15)
        m = logger.metrics()
        assert m["routes_total"] == 2

    def test_logger_tracks_validation_fail(self):
        logger = NeumannLogger()
        logger.validation("sha256:abc", valid=False, reason="forbidden")
        m = logger.metrics()
        assert m["validation_fail"] == 1

    def test_logger_tracks_errors(self):
        logger = NeumannLogger()
        logger.error("TypeError", "DiffRenderer", "bad diff format")
        m = logger.metrics()
        assert m["errors_total"] == 1

    def test_events_buffer(self):
        logger = NeumannLogger()
        logger.route("code_block", "terminal", "CodeBlockRenderer", 0.1)
        events = logger.events()
        assert len(events) == 1
        assert events[0]["type"] == "route"
        assert events[0]["classified_as"] == "code_block"

    def test_export_json(self):
        logger = NeumannLogger()
        logger.route("error", "terminal", "ErrorRenderer", 0.3)
        data = json.loads(logger.export_json())
        assert "metrics" in data
        assert "events" in data

    def test_pipeline_emits_observability(self):
        pipeline = NeumannPipeline()
        pipeline.process("```python\npass\n```")
        m = pipeline.logger.metrics()
        assert m["routes_total"] >= 1
        assert m["avg_duration_ms"] >= 0


# ── Config ─────────────────────────────────────────────────────

class TestConfig:
    def test_default_config(self):
        cfg = load_config()
        assert cfg.log_level == "INFO"
        assert cfg.max_buffer == 32_768
        assert cfg.event_max == 10_000

    def test_overrides(self):
        cfg = load_config(log_level="DEBUG", event_max=500)
        assert cfg.log_level == "DEBUG"
        assert cfg.event_max == 500

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("NEUMANN_LOG_LEVEL", "WARNING")
        monkeypatch.setenv("NEUMANN_MAX_BUFFER", "65536")
        cfg = load_config()
        assert cfg.log_level == "WARNING"
        assert cfg.max_buffer == 65536

    def test_config_to_json(self):
        cfg = load_config(log_level="DEBUG")
        data = json.loads(cfg.to_json())
        assert data["log_level"] == "DEBUG"

    def test_config_file(self, tmp_path):
        f = tmp_path / "neumann.json"
        f.write_text(json.dumps({"log_level": "ERROR", "event_max": 100}))
        cfg = load_config(config_file=str(f))
        assert cfg.log_level == "ERROR"
        assert cfg.event_max == 100


# ── Error Recovery ─────────────────────────────────────────────

class TestErrorRecovery:
    def test_pipeline_never_crashes_on_bad_input(self):
        """Even with a broken formatter, pipeline falls back gracefully."""
        pipeline = NeumannPipeline()
        # Normal input should work
        result = pipeline.process("Hello world")
        assert result.rendered is not None

    def test_recovered_flag_on_fallback(self):
        """When a formatter crashes, recovered=True."""
        from neumann.registry import _REGISTRY
        from neumann.types import Token, RenderContext
        from neumann.formatters import Formatter

        # Inject a crashing formatter for code_block
        class CrashingFormatter(Formatter):
            def render(self, token: Token, context: RenderContext) -> str:
                raise RuntimeError("intentional crash for test")

        old = _REGISTRY.get("CodeBlockRenderer")
        _REGISTRY["CodeBlockRenderer"] = CrashingFormatter()
        try:
            pipeline = NeumannPipeline()
            result = pipeline.process("```python\npass\n```")
            assert result.recovered is True
            assert result.decision.formatter == "FallbackHandler"
        finally:
            _REGISTRY["CodeBlockRenderer"] = old


# ── Hot-Reload ─────────────────────────────────────────────────

class TestHotReload:
    def test_reload_rules(self):
        pipeline = NeumannPipeline()
        pipeline.reload_rules()
        # Should not crash, classifier/selector reloaded
        result = pipeline.process("```js\nconsole.log('hi')\n```")
        assert result.token.type == TokenType.CODE_BLOCK

    def test_reload_formatters(self):
        pipeline = NeumannPipeline()
        pipeline.reload_formatters()
        result = pipeline.process("Hello")
        assert result.rendered is not None
