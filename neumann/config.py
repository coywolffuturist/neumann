"""Configuration management for Neumann.

Loads defaults from environment variables or a JSON config file.
All config is immutable after construction — change it by creating a new instance.

Environment variables (all optional):
    NEUMANN_CONTEXT          — default RenderContext override
    NEUMANN_LOG_LEVEL        — "DEBUG" | "INFO" | "WARNING" | "ERROR"
    NEUMANN_MAX_BUFFER       — streaming buffer size in bytes (default 32768)
    NEUMANN_RULES_PATH       — path to token_rules.json
    NEUMANN_DISPATCH_PATH    — path to dispatch.json
    NEUMANN_OUTPUT_SCHEMA    — JSON string of default output schema
    NEUMANN_EVENT_MAX        — max events in logger buffer (default 10000)
    NEUMANN_CONFIG_FILE      — path to a JSON config file (overrides env vars)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


_DEFAULTS: dict[str, Any] = {
    "context": None,
    "log_level": "INFO",
    "max_buffer": 32_768,
    "rules_path": None,
    "dispatch_path": None,
    "output_schema": {},
    "event_max": 10_000,
    "config_file": None,
}


@dataclass(frozen=True)
class NeumannConfig:
    """Immutable configuration container for Neumann."""

    context: str | None = None
    log_level: str = "INFO"
    max_buffer: int = 32_768
    rules_path: str | None = None
    dispatch_path: str | None = None
    output_schema: dict[str, Any] = field(default_factory=dict)
    event_max: int = 10_000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def load_config(
    config_file: str | Path | None = None,
    **overrides: Any,
) -> NeumannConfig:
    """Load configuration from file + environment + overrides.

    Priority (highest wins):
    1. `overrides` kwargs
    2. Environment variables
    3. Config file (JSON)
    4. Defaults
    """
    cfg = dict(_DEFAULTS)

    # 3. Config file
    file_path = Path(config_file) if config_file else None
    if file_path and file_path.exists():
        with open(file_path) as f:
            file_cfg = json.load(f)
        cfg.update(file_cfg)

    # Also check NEUMANN_CONFIG_FILE env
    env_file = os.environ.get("NEUMANN_CONFIG_FILE")
    if env_file and not config_file:
        path = Path(env_file)
        if path.exists():
            with open(path) as f:
                cfg.update(json.load(f))

    # 2. Environment variables
    env_map = {
        "NEUMANN_CONTEXT":        "context",
        "NEUMANN_LOG_LEVEL":      "log_level",
        "NEUMANN_MAX_BUFFER":     "max_buffer",
        "NEUMANN_RULES_PATH":     "rules_path",
        "NEUMANN_DISPATCH_PATH":  "dispatch_path",
        "NEUMANN_EVENT_MAX":      "event_max",
    }
    for env_var, key in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            cfg[key] = int(val) if key in ("max_buffer", "event_max") else val

    # 1. Overrides
    cfg.update(overrides)

    return NeumannConfig(
        context=cfg["context"],
        log_level=cfg["log_level"],
        max_buffer=cfg["max_buffer"],
        rules_path=cfg["rules_path"],
        dispatch_path=cfg["dispatch_path"],
        output_schema=cfg["output_schema"],
        event_max=cfg["event_max"],
    )
