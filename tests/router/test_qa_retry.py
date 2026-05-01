"""Tests for the QA retry / escalation policy.

Spec: ``docs/specs/qa-agent.md`` § "Retry / escalation".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neumann.router.qa_retry import (
    DEFAULT_MAX_RETRIES,
    ENV_OVERRIDE,
    RetryAction,
    RetryPolicy,
    load_policy,
)


# ── decision logic ──────────────────────────────────────────────────────────


def test_pass_is_done() -> None:
    assert RetryPolicy().decide(verdict="PASS", attempt=1) == RetryAction.DONE


def test_skip_is_done() -> None:
    assert RetryPolicy().decide(verdict="SKIP", attempt=1) == RetryAction.DONE


def test_first_fail_retries() -> None:
    assert RetryPolicy().decide(verdict="FAIL", attempt=1) == RetryAction.RETRY


def test_second_fail_retries() -> None:
    assert RetryPolicy().decide(verdict="FAIL", attempt=2) == RetryAction.RETRY


def test_third_fail_escalates() -> None:
    """At max_retries=2, attempt=3 means we've used 2 retries — escalate."""
    assert RetryPolicy().decide(verdict="FAIL", attempt=3) == RetryAction.PAUSE_ESCALATE


def test_subsequent_fails_keep_escalating() -> None:
    """If a paused task somehow gets re-run, never silently retry past threshold."""
    assert RetryPolicy().decide(verdict="FAIL", attempt=10) == RetryAction.PAUSE_ESCALATE


def test_planner_bug_escalates_immediately() -> None:
    """Planner bugs cannot be self-healed by retry — escalate on first occurrence."""
    assert RetryPolicy().decide(verdict="PLANNER_BUG", attempt=1) == RetryAction.PAUSE_ESCALATE


def test_unknown_verdict_escalates_for_safety() -> None:
    """Never silently treat an unknown verdict as PASS."""
    assert RetryPolicy().decide(verdict="WAT", attempt=1) == RetryAction.PAUSE_ESCALATE


def test_zero_retries_means_first_fail_escalates() -> None:
    p = RetryPolicy(max_retries=0)
    assert p.decide(verdict="FAIL", attempt=1) == RetryAction.PAUSE_ESCALATE


def test_high_retry_threshold() -> None:
    p = RetryPolicy(max_retries=5)
    for n in range(1, 6):
        assert p.decide(verdict="FAIL", attempt=n) == RetryAction.RETRY
    assert p.decide(verdict="FAIL", attempt=6) == RetryAction.PAUSE_ESCALATE


def test_verdict_case_insensitive() -> None:
    assert RetryPolicy().decide(verdict="pass", attempt=1) == RetryAction.DONE
    assert RetryPolicy().decide(verdict="fail", attempt=1) == RetryAction.RETRY
    assert RetryPolicy().decide(verdict="planner_bug", attempt=1) == RetryAction.PAUSE_ESCALATE


# ── load_policy resolution order ────────────────────────────────────────────


def test_load_policy_default_when_no_config(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    p = load_policy(config_path=missing, env={})
    assert p.max_retries == DEFAULT_MAX_RETRIES


def test_load_policy_env_override_wins(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"max_qa_retries": 7}))
    p = load_policy(config_path=cfg, env={ENV_OVERRIDE: "1"})
    assert p.max_retries == 1


def test_load_policy_reads_fusion_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"max_qa_retries": 5, "unrelated": "x"}))
    p = load_policy(config_path=cfg, env={})
    assert p.max_retries == 5


def test_load_policy_falls_back_to_default_on_malformed_env(tmp_path: Path) -> None:
    p = load_policy(config_path=tmp_path / "missing.json", env={ENV_OVERRIDE: "abc"})
    assert p.max_retries == DEFAULT_MAX_RETRIES


def test_load_policy_falls_back_to_default_on_malformed_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json")
    p = load_policy(config_path=cfg, env={})
    assert p.max_retries == DEFAULT_MAX_RETRIES


def test_load_policy_negative_value_falls_back_to_default(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"max_qa_retries": -3}))
    p = load_policy(config_path=cfg, env={})
    assert p.max_retries == DEFAULT_MAX_RETRIES


def test_load_policy_non_int_value_falls_back_to_default(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"max_qa_retries": "two"}))
    p = load_policy(config_path=cfg, env={})
    assert p.max_retries == DEFAULT_MAX_RETRIES


def test_load_policy_zero_is_honored_not_falsy_fallback(tmp_path: Path) -> None:
    """``max_qa_retries: 0`` is a valid setting (no retries). Don't truthy-check."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"max_qa_retries": 0}))
    p = load_policy(config_path=cfg, env={})
    assert p.max_retries == 0


# ── parametric verdict matrix ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "verdict,attempt,expected",
    [
        ("PASS", 1, RetryAction.DONE),
        ("PASS", 99, RetryAction.DONE),
        ("SKIP", 1, RetryAction.DONE),
        ("FAIL", 1, RetryAction.RETRY),
        ("FAIL", 2, RetryAction.RETRY),
        ("FAIL", 3, RetryAction.PAUSE_ESCALATE),
        ("PLANNER_BUG", 1, RetryAction.PAUSE_ESCALATE),
        ("PLANNER_BUG", 99, RetryAction.PAUSE_ESCALATE),
    ],
)
def test_decision_matrix(verdict: str, attempt: int, expected: RetryAction) -> None:
    assert RetryPolicy().decide(verdict=verdict, attempt=attempt) == expected
