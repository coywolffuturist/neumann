"""Neumann CLI — run the symbolic routing kernel from the terminal.

Usage:
    python -m neumann.cli                          # interactive mode (stdin)
    python -m neumann.cli --config config.json     # with config file
    python -m neumann.cli --metrics                 # show metrics & exit
    echo '{"tool":"bash","input":{}}' | python -m neumann.cli  # pipe mode
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .pipeline import NeumannPipeline
from .logger import NeumannLogger


def _interactive(pipeline: NeumannPipeline, logger: NeumannLogger) -> None:
    """Read from stdin line by line, process and print."""
    print("Neumann CLI — interactive mode. Send input (Ctrl+D to quit).\n")
    try:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                continue
            result = pipeline.process(line)
            print(result.rendered)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n── Neumann Metrics ──")
        print(json.dumps(logger.metrics(), indent=2))


def _pipe_mode(pipeline: NeumannPipeline, as_json: bool = False) -> None:
    """Read all stdin as a single input, output rendered result + JSON event."""
    raw = sys.stdin.read()
    if not raw.strip():
        print("No input.", file=sys.stderr)
        sys.exit(1)
    result = pipeline.process(raw)
    print(result.rendered)
    if as_json:
        print("\n--- event ---")
        print(json.dumps({
            "token_type": result.token.type.value,
            "context": result.context.value,
            "formatter": result.decision.formatter,
            "validation": {"valid": result.validation.valid, "reason": result.validation.reason},
            "duration_ms": result.duration_ms,
            "input_hash": result.input_hash,
        }, indent=2))


def _show_metrics(logger: NeumannLogger) -> None:
    print(json.dumps(logger.metrics(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="neumann",
        description="Neumann — deterministic symbolic routing kernel for AI agents",
    )
    parser.add_argument("--config", type=str, help="Path to Neumann config JSON file")
    parser.add_argument("--metrics", action="store_true", help="Show metrics and exit")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output routing event as JSON (pipe mode)")
    args = parser.parse_args()

    # Load config
    cfg = load_config(config_file=args.config)
    logger = NeumannLogger(level=cfg.log_level, max_events=cfg.event_max)

    # Build pipeline
    from .classifier import TokenClassifier
    from .context import ContextResolver
    from .selector import FormatSelector
    from .validator import SchemaValidator

    classifier = TokenClassifier(rules_path=cfg.rules_path) if cfg.rules_path else None
    selector = FormatSelector(dispatch_path=cfg.dispatch_path) if cfg.dispatch_path else None
    validator = SchemaValidator() if cfg.output_schema else None

    env = {}
    if cfg.context:
        env["context"] = cfg.context

    pipeline = NeumannPipeline(
        classifier=classifier,
        resolver=ContextResolver(),
        selector=selector,
        validator=validator,
        output_schema=cfg.output_schema,
        logger=logger,
    )

    if args.metrics:
        _show_metrics(logger)
        return

    # If stdin is a tty → interactive, else → pipe mode
    if sys.stdin.isatty():
        _interactive(pipeline, logger)
    else:
        _pipe_mode(pipeline, as_json=args.as_json)


if __name__ == "__main__":
    main()
