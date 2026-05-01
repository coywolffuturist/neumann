"""Tests for the intent-level Decomposer pipeline stage.

Decomposer takes a ConfirmedIntent and returns a list of ConfirmedIntents:
  - Below threshold: returns ``[intent]`` (passthrough — Planner runs once).
  - Above threshold: returns ``[child_1, ..., child_N, integration]``.

Covers:
  - below-threshold passthrough
  - above-threshold split (by success_criteria, by file count, by verb count, by length)
  - children carry parent_intent_id and unique sub_intent_id
  - integration intent records dependencies on every child
  - rules JSON loader handles missing / malformed / partial files
  - inline thresholds override rules file
  - pathological case (over threshold but no clean seams) returns intent unchanged
"""
from __future__ import annotations

import json
from pathlib import Path

from neumann.router import ConfirmedIntent
from neumann.router.decomposer import Decomposer, _DEFAULT_THRESHOLDS


# ── helpers ───────────────────────────────────────────────────


def _intent(
    confirmed_intent: str = "Add a /api/healthz endpoint",
    success_criteria: tuple[str, ...] = (),
    target_repo: str = "owner/repo",
    constraints: tuple[str, ...] = (),
) -> ConfirmedIntent:
    return ConfirmedIntent(
        raw_prompt=confirmed_intent,
        confirmed_intent=confirmed_intent,
        target_repo=target_repo,
        success_criteria=success_criteria,
        constraints=constraints,
        human_approved=True,
    )


# ── below-threshold passthrough ───────────────────────────────


def test_small_intent_passes_through_unchanged() -> None:
    decomposer = Decomposer()
    intent = _intent("Add a /api/healthz endpoint that returns {ok:true}.")
    out = decomposer.decompose(intent)
    assert len(out) == 1
    assert out[0] is intent


def test_intent_with_one_success_criterion_passes_through() -> None:
    decomposer = Decomposer()
    intent = _intent(
        "Fix the typo in README.",
        success_criteria=("README has correct spelling of 'environment'.",),
    )
    out = decomposer.decompose(intent)
    assert len(out) == 1
    assert out[0] is intent


# ── above-threshold split by success_criteria ─────────────────


def test_oversized_by_explicit_criteria_splits_into_n_plus_one() -> None:
    decomposer = Decomposer()
    intent = _intent(
        "Refactor the server into modular routes.",
        success_criteria=(
            "Create app/routes/intel.js with /add /graph /list",
            "Create app/routes/quests.js with all quest endpoints",
            "Create app/routes/pack.js with all pack endpoints",
            "Create app/routes/coywolf.js with identity/processes/metrics",
            "Create app/routes/wallet.js with history/snapshot",
        ),
    )
    out = decomposer.decompose(intent)
    # 5 criteria → 5 children + 1 integration = 6 total
    assert len(out) == 6


def test_split_children_carry_parent_id_and_unique_sub_intent_ids() -> None:
    decomposer = Decomposer()
    intent = _intent(
        "Big refactor",
        success_criteria=tuple(f"Criterion {i}" for i in range(5)),
    )
    out = decomposer.decompose(intent)
    children = out[:-1]
    parent_ids = {c.extra["parent_intent_id"] for c in children}
    sub_ids = [c.extra["sub_intent_id"] for c in children]
    assert len(parent_ids) == 1, "all children share one parent_intent_id"
    assert len(sub_ids) == len(set(sub_ids)), "child sub_intent_ids are unique"
    for c in children:
        assert c.extra["decomposed"] is True
        assert "is_integration" not in c.extra


def test_integration_intent_depends_on_every_child() -> None:
    decomposer = Decomposer()
    intent = _intent(
        "Big refactor",
        success_criteria=tuple(f"Criterion {i}" for i in range(4)),
    )
    out = decomposer.decompose(intent)
    integration = out[-1]
    children = out[:-1]
    assert integration.extra.get("is_integration") is True
    assert (
        integration.extra["parent_intent_id"]
        == children[0].extra["parent_intent_id"]
    )
    expected_deps = tuple(c.extra["sub_intent_id"] for c in children)
    assert integration.extra["depends_on_sub_intents"] == expected_deps


def test_each_child_has_one_focused_success_criterion() -> None:
    decomposer = Decomposer()
    intent = _intent(
        "Big refactor",
        success_criteria=("A", "B", "C", "D", "E"),
    )
    out = decomposer.decompose(intent)
    children = out[:-1]
    for i, child in enumerate(children):
        assert child.success_criteria == (intent.success_criteria[i],)


# ── above-threshold split by inferred seams ───────────────────


def test_oversized_by_distinct_outputs_triggers_split() -> None:
    # Many verbs in the prose, criteria absent. Triggers verb-count threshold.
    intent = _intent(
        "Build the signup pipeline. "
        "Create the signup endpoint. Add email validation. "
        "Implement the password hash flow. Build the onboarding redirect. "
        "Generate confirmation email. Write the rate-limit middleware."
    )
    decomposer = Decomposer()
    out = decomposer.decompose(intent)
    # 6+ output verbs > default max_distinct_outputs (2). Should split.
    assert len(out) > 1
    # Last is integration
    assert out[-1].extra.get("is_integration") is True


def test_oversized_by_file_count_triggers_split() -> None:
    intent = _intent(
        "Touch app/server.js, app/routes/intel.js, app/routes/quests.js, "
        "app/routes/pack.js, app/routes/wallet.js."
    )
    decomposer = Decomposer()
    out = decomposer.decompose(intent)
    # 5 distinct file mentions > default max_files_changed (3). Should split.
    assert len(out) > 1


def test_oversized_by_length_triggers_split() -> None:
    long_text = "Implement the new module. " * 200  # ~5000 chars → ~100 lines
    intent = _intent(long_text)
    decomposer = Decomposer(thresholds={"max_lines_estimate": 50})
    out = decomposer.decompose(intent)
    assert len(out) > 1


def test_seam_inference_prefers_bullets() -> None:
    """When prose has bullet items and criteria absent, split along bullets."""
    intent = _intent(
        "Refactor stuff:\n"
        "- Build the helpers module\n"
        "- Build the intel module\n"
        "- Build the quests module\n"
        "- Build the pack module"
    )
    decomposer = Decomposer()
    out = decomposer.decompose(intent)
    # 4 bullets + integration
    children = out[:-1]
    assert len(children) >= 2  # at least split happened
    # Children's criteria reflect the bullets
    bullet_texts = {c.success_criteria[0] for c in children}
    for expected in ("Build the helpers module", "Build the intel module"):
        assert any(expected in b for b in bullet_texts), \
            f"expected bullet '{expected}' to seed a child"


# ── pathological cases ───────────────────────────────────────


def test_over_threshold_but_no_clean_seams_returns_unchanged() -> None:
    # A long blob with no bullets, no verb-led sentences, no file mentions —
    # exceeds line threshold but nothing to split along. Should return [intent].
    intent = _intent(
        "x" * 10000,  # over line threshold but no structure
        target_repo="owner/repo",
    )
    decomposer = Decomposer(thresholds={"max_lines_estimate": 10})
    out = decomposer.decompose(intent)
    assert len(out) == 1
    assert out[0] is intent


# ── threshold injection ───────────────────────────────────────


def test_inline_thresholds_override_rules_file(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"max_distinct_outputs": 1}))
    decomposer = Decomposer(
        rules_path=rules_file,
        thresholds={"max_distinct_outputs": 100},
    )
    intent = _intent(
        "Big task",
        success_criteria=("A", "B", "C", "D", "E"),
    )
    out = decomposer.decompose(intent)
    # 5 criteria < 100 threshold → no split
    assert len(out) == 1


# ── rules loader fault-tolerance ──────────────────────────────


def test_loader_falls_back_to_defaults_on_missing_file(tmp_path: Path) -> None:
    decomposer = Decomposer(rules_path=tmp_path / "does-not-exist.json")
    assert decomposer._thresholds == _DEFAULT_THRESHOLDS


def test_loader_falls_back_to_defaults_on_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{")
    decomposer = Decomposer(rules_path=bad)
    assert decomposer._thresholds == _DEFAULT_THRESHOLDS


def test_loader_merges_partial_rules_with_defaults(tmp_path: Path) -> None:
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"max_files_changed": 99}))
    decomposer = Decomposer(rules_path=partial)
    assert decomposer._thresholds["max_files_changed"] == 99
    # Other keys unchanged
    assert (
        decomposer._thresholds["max_lines_estimate"]
        == _DEFAULT_THRESHOLDS["max_lines_estimate"]
    )
    assert (
        decomposer._thresholds["max_distinct_outputs"]
        == _DEFAULT_THRESHOLDS["max_distinct_outputs"]
    )


def test_loader_ignores_invalid_value_types(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.json"
    bogus.write_text(
        json.dumps({"max_files_changed": "lots", "max_lines_estimate": -1})
    )
    decomposer = Decomposer(rules_path=bogus)
    assert decomposer._thresholds == _DEFAULT_THRESHOLDS


# ── intent-metadata invariants ────────────────────────────────


def test_split_preserves_intent_metadata_on_children() -> None:
    decomposer = Decomposer()
    intent = ConfirmedIntent(
        raw_prompt="raw",
        confirmed_intent="Big refactor",
        target_repo="owner/repo",
        success_criteria=("A", "B", "C", "D"),
        constraints=("must use ESM",),
        out_of_scope=("don't touch CSS",),
        human_approver_id="U123",
        human_approved=True,
    )
    out = decomposer.decompose(intent)
    children = out[:-1]
    for c in children:
        assert c.target_repo == "owner/repo"
        assert c.constraints == ("must use ESM",)
        assert c.out_of_scope == ("don't touch CSS",)
        assert c.human_approver_id == "U123"
        assert c.human_approved is True
        assert c.raw_prompt == "raw"
