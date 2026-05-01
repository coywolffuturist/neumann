"""fnr — the Neumann router CLI.

Two modes:

    fnr task "<prompt>"            — classify + route a single-task prompt.
    fnr mission "<prompt>"         — invoke planner, route each planned task.
    fnr classify-shape "<prompt>"  — just print the shape decision.

In v1 this only PRINTS the routing decisions. Wiring into Fusion's
`fn_task_create` (with --agent <persona>) is a follow-up step — see
`neumann/router/README.md`.

The default planner is the offline ``MockPlanner`` (no fixtures registered),
which produces a single-task plan derived from the prompt. Real LLM-backed
planners plug in by setting ``planner=`` on ``RouterPipeline``.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .interviewer import CLIInterviewer
from .pipeline import PipelineResult, RouterPipeline
from .types import PlannedTask, Shape


def _serialize_trace(trace_obj: Any) -> dict[str, Any]:
    if hasattr(trace_obj, "__dict__") or hasattr(trace_obj, "__dataclass_fields__"):
        try:
            return _scrub(asdict(trace_obj))
        except TypeError:
            pass
    return {"value": str(trace_obj)}


def _scrub(obj: Any) -> Any:
    """Convert enums + tuples for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    if hasattr(obj, "value") and hasattr(obj, "name"):
        return obj.value
    return obj


def _print_result(result: PipelineResult, *, json_out: bool) -> None:
    if json_out:
        payload = {
            "shape": result.shape_decision.shape.value,
            "shape_priority": result.shape_decision.matched_rule_priority,
            "confirmed_intent": (
                {
                    "confirmed_intent": result.confirmed_intent.confirmed_intent,
                    "target_repo": result.confirmed_intent.target_repo,
                    "success_criteria": list(result.confirmed_intent.success_criteria),
                    "constraints": list(result.confirmed_intent.constraints),
                    "out_of_scope": list(result.confirmed_intent.out_of_scope),
                    "human_approved": result.confirmed_intent.human_approved,
                }
                if result.confirmed_intent
                else None
            ),
            "plan": (
                {
                    "mission_title": result.plan.mission_title,
                    "summary": result.plan.summary,
                    "assumptions": list(result.plan.assumptions),
                    "tasks": [_scrub(asdict(t)) for t in result.plan.tasks],
                }
                if result.plan
                else None
            ),
            "routes": [_scrub(asdict(r)) for r in result.routes],
        }
        print(json.dumps(payload, indent=2, default=str))
        return

    sd = result.shape_decision
    print(f"\nshape: {sd.shape.value}  (rule priority {sd.matched_rule_priority})")
    if result.confirmed_intent is not None:
        ci = result.confirmed_intent
        print(f"\nconfirmed intent: {ci.confirmed_intent}")
        print(f"  target repo:     {ci.target_repo}")
        print(f"  success:         {'; '.join(ci.success_criteria) or '(none)'}")
        if ci.constraints:
            print(f"  constraints:     {'; '.join(ci.constraints)}")
        if ci.out_of_scope:
            print(f"  out of scope:    {'; '.join(ci.out_of_scope)}")
        print(f"  approved:        {ci.human_approved}")

    if sd.shape == Shape.MISSION and result.plan:
        print(f"\nplan: {result.plan.mission_title}")
        if result.plan.summary:
            print(f"  summary: {result.plan.summary}")
        if result.plan.assumptions:
            print("  assumptions:")
            for a in result.plan.assumptions:
                print(f"    - {a}")
        print()
    for i, route in enumerate(result.routes, 1):
        d = route.persona_decision
        print(f"task {i}: {route.task.title}")
        print(f"  → persona:  {d.persona}")
        print(f"  → type:     {d.task_type.value}")
        print(f"  → context:  project_type={route.context.project_type}")
        print(f"  → fallback: {d.fallback_used}")
        print(f"  → trace:")
        for line in d.trace:
            print(f"      {line}")
        print(f"  → ({route.duration_ms:.2f} ms, {route.input_hash})")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fnr", description="Neumann router — deterministic persona routing.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output.")
    parser.add_argument(
        "--interview",
        action="store_true",
        help="Run the interactive interview before planning/routing. Recommended for any human-driven entry point.",
    )
    parser.add_argument(
        "--allowed-org",
        action="append",
        dest="allowed_orgs",
        default=[],
        help="Org allowlist for repo-spec validation (e.g. --allowed-org pakt-world). Repeatable.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_task = sub.add_parser("task", help="Route a single-task prompt.")
    p_task.add_argument("prompt", help="The task prompt.")

    p_mission = sub.add_parser("mission", help="Plan + route a mission prompt.")
    p_mission.add_argument("prompt", help="The mission prompt.")

    p_shape = sub.add_parser("classify-shape", help="Print only the shape decision.")
    p_shape.add_argument("prompt", help="The user prompt.")

    args = parser.parse_args(argv)

    interviewer = None
    if args.interview:
        interviewer = CLIInterviewer(allowed_orgs=tuple(args.allowed_orgs))

    pipeline = RouterPipeline(
        interviewer=interviewer,
        allowed_orgs=tuple(args.allowed_orgs),
    )

    if args.cmd == "classify-shape":
        sd = pipeline.classify_shape(args.prompt)
        if args.json:
            print(json.dumps({
                "shape": sd.shape.value,
                "priority": sd.matched_rule_priority,
                "note": sd.matched_rule_note,
                "sentence_count": sd.sentence_count,
            }, indent=2))
        else:
            print(f"shape: {sd.shape.value}  (priority {sd.matched_rule_priority}, sentences={sd.sentence_count})")
            print(f"note:  {sd.matched_rule_note}")
        return 0

    if args.cmd == "task":
        if args.interview:
            # Full pipeline (interview → maybe plan → route).
            result = pipeline.process(args.prompt)
        else:
            # Force single-task path even if shape classifier disagrees.
            trace = pipeline.route(PlannedTask.from_prompt(args.prompt))
            result = PipelineResult(
                shape_decision=pipeline.classify_shape(args.prompt),
                confirmed_intent=None,
                plan=None,
                routes=(trace,),
            )
        _print_result(result, json_out=args.json)
        return 0

    if args.cmd == "mission":
        result = pipeline.process(args.prompt)
        _print_result(result, json_out=args.json)
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
