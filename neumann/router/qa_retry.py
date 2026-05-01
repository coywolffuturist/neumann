"""Retry / escalation policy for the QA gate.

Pure functions. No I/O. Honored by both pre-merge (Phase 2) and post-deploy
(Phase 3) executors so the contract is uniform.

Policy per ``docs/specs/qa-agent.md`` § "Retry / escalation":

| Failure count | Action                                                           |
|---------------|------------------------------------------------------------------|
| 1             | RETRY — bounce back to In Progress with failure context appended |
| 2             | RETRY — same                                                     |
| 3 (= 2 retries exhausted) | PAUSE_ESCALATE — pause + WhatsApp ping Brendan       |
| 4+            | should never reach here; treated as PAUSE_ESCALATE for safety    |

Threshold is tunable via ``~/.fusion/config.json`` ``max_qa_retries`` (default 2)
or env var ``NEUMANN_QA_MAX_RETRIES``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

DEFAULT_MAX_RETRIES = 2
DEFAULT_FUSION_CONFIG = Path.home() / ".fusion" / "config.json"
ENV_OVERRIDE = "NEUMANN_QA_MAX_RETRIES"


class RetryAction(str, Enum):
    """Decision for what to do after a single QA attempt."""

    DONE = "done"  # PASS or SKIP — no further action needed.
    RETRY = "retry"  # FAIL, retries left — bounce to In Progress with context.
    PAUSE_ESCALATE = "pause_escalate"  # Retries exhausted OR PLANNER_BUG — pause + ping.


@dataclass(frozen=True)
class RetryPolicy:
    """Pure-function retry policy. Default: 2 retries (3 total attempts)."""

    max_retries: int = DEFAULT_MAX_RETRIES

    def decide(self, *, verdict: str, attempt: int) -> RetryAction:
        """Return the action to take given a verdict and the 1-indexed attempt number.

        ``attempt`` is the count of attempts made so far INCLUDING the current
        one. So ``attempt=1`` is the first attempt; if it fails, retries left
        = max_retries.
        """
        v = verdict.upper()
        if v in ("PASS", "SKIP"):
            return RetryAction.DONE
        if v == "PLANNER_BUG":
            # Planner bugs cannot be auto-fixed by re-running; escalate immediately.
            return RetryAction.PAUSE_ESCALATE
        if v == "FAIL":
            retries_used = attempt  # number of failures so far (current included)
            if retries_used > self.max_retries:
                return RetryAction.PAUSE_ESCALATE
            return RetryAction.RETRY
        # Unknown verdict from upstream — treat as escalation; never silently pass.
        return RetryAction.PAUSE_ESCALATE


def load_policy(
    config_path: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> RetryPolicy:
    """Load retry policy from env override > Fusion config > default.

    Resolution order (first non-empty wins):
    1. ``NEUMANN_QA_MAX_RETRIES`` env var (decimal int)
    2. ``max_qa_retries`` key in ``~/.fusion/config.json``
    3. ``DEFAULT_MAX_RETRIES`` (2)

    Malformed values fall through to the next source rather than raising —
    a typo in config should not brick the QA gate.
    """
    env = env if env is not None else os.environ  # type: ignore[assignment]
    raw = env.get(ENV_OVERRIDE, "").strip()
    if raw:
        try:
            return RetryPolicy(max_retries=int(raw))
        except ValueError:
            pass

    path = Path(config_path) if config_path else DEFAULT_FUSION_CONFIG
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            val = data.get("max_qa_retries")
            if isinstance(val, int) and val >= 0:
                return RetryPolicy(max_retries=val)
        except (json.JSONDecodeError, OSError):
            pass

    return RetryPolicy()
