"""Tests for ``parse_qa_test`` — the ``## QA Test`` PROMPT.md parser.

Spec: ``coywolffuturist/neumann:docs/specs/qa-agent.md``.
"""
from __future__ import annotations

import pytest

from neumann.router import (
    QATest,
    QATestParseError,
    QATestType,
    ReviewerTier,
    parse_qa_test,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _qa_section(
    *,
    type_: str = "browser",
    tier: str = "both",
    pre_merge_model: str | None = "claude-opus-4-7",
    post_deploy_model: str | None = "qwen-3.6",
    pass_criterion: str = "all assertions pass",
    browser_tool: str | None = "agent-browser --show",
    steps: tuple[str, ...] = (
        "Open the file `app/public/dashboard.html` in the worktree.",
        "Open `http://localhost:7777/intel` via `agent-browser --show`.",
        "Click `.intel-node[data-id=\"n42\"]`.",
        "Assert: `document.getElementById('intel-graph-tooltip').style.display === 'block'`.",
    ),
    expected_failures: tuple[str, ...] = (
        "Missing `pinnedNodeData` reference → missing-implementation failure.",
        "Tooltip vanishes on mouseout while pinned → behavior-incorrect failure.",
    ),
) -> str:
    lines: list[str] = ["## QA Test", ""]
    lines.append(f"**Type:** {type_}")
    lines.append(f"**Reviewer tier:** {tier}")
    if pre_merge_model is not None:
        lines.append(f"**Pre-merge model:** {pre_merge_model}")
    if post_deploy_model is not None:
        lines.append(f"**Post-deploy model:** {post_deploy_model}")
    lines.append(f"**Pass criterion:** {pass_criterion}")
    if browser_tool is not None:
        lines.append(f"**Browser tool:** {browser_tool}")
    lines.append("")
    lines.append("### Steps")
    lines.append("")
    for i, s in enumerate(steps, start=1):
        lines.append(f"{i}. {s}")
    if expected_failures:
        lines.append("")
        lines.append("### Expected failure modes")
        lines.append("")
        for f in expected_failures:
            lines.append(f"- {f}")
    return "\n".join(lines) + "\n"


def _wrap_in_prompt(qa_section: str) -> str:
    return (
        "# Task: Pin intel graph tooltips\n\n"
        "## Goal\n\n"
        "Make the tooltip persist when a node is clicked.\n\n"
        + qa_section
        + "\n## Notes\n\nSee dashboard.html line 4296.\n"
    )


# ── happy path ─────────────────────────────────────────────────────────────


def test_happy_path_returns_fully_populated_qatest() -> None:
    md = _wrap_in_prompt(_qa_section())
    qa = parse_qa_test(md)

    assert isinstance(qa, QATest)
    assert qa.type == QATestType.BROWSER
    assert qa.reviewer_tier == ReviewerTier.BOTH
    assert qa.pre_merge_model == "claude-opus-4-7"
    assert qa.post_deploy_model == "qwen-3.6"
    assert qa.pass_criterion == "all assertions pass"
    assert qa.browser_tool == "agent-browser --show"
    assert len(qa.steps) == 4
    assert qa.steps[0].startswith("Open the file")
    assert qa.steps[3].startswith("Assert:")
    assert len(qa.expected_failures) == 2
    assert qa.runs_pre_merge
    assert qa.runs_post_deploy


def test_pre_merge_only_skips_post_deploy_model_requirement() -> None:
    md = _wrap_in_prompt(
        _qa_section(tier="pre-merge", post_deploy_model=None)
    )
    qa = parse_qa_test(md)
    assert qa.reviewer_tier == ReviewerTier.PRE_MERGE
    assert qa.post_deploy_model is None
    assert qa.runs_pre_merge
    assert not qa.runs_post_deploy


def test_post_deploy_only_skips_pre_merge_model_requirement() -> None:
    md = _wrap_in_prompt(
        _qa_section(tier="post-deploy", pre_merge_model=None)
    )
    qa = parse_qa_test(md)
    assert qa.reviewer_tier == ReviewerTier.POST_DEPLOY
    assert qa.pre_merge_model is None
    assert qa.runs_post_deploy
    assert not qa.runs_pre_merge


@pytest.mark.parametrize(
    "type_value,enum_value",
    [
        ("browser", QATestType.BROWSER),
        ("behavior", QATestType.BEHAVIOR),
        ("refactor-equivalence", QATestType.REFACTOR_EQUIVALENCE),
        ("copy-review", QATestType.COPY_REVIEW),
        ("research-soundness", QATestType.RESEARCH_SOUNDNESS),
    ],
)
def test_all_type_values_parse(type_value: str, enum_value: QATestType) -> None:
    # Non-browser types don't require a browser_tool, so omit it for those.
    md = _wrap_in_prompt(
        _qa_section(
            type_=type_value,
            browser_tool="agent-browser --show" if type_value == "browser" else None,
        )
    )
    qa = parse_qa_test(md)
    assert qa.type == enum_value


@pytest.mark.parametrize(
    "tier_value,enum_value",
    [
        ("pre-merge", ReviewerTier.PRE_MERGE),
        ("post-deploy", ReviewerTier.POST_DEPLOY),
        ("both", ReviewerTier.BOTH),
    ],
)
def test_all_reviewer_tier_values_parse(
    tier_value: str, enum_value: ReviewerTier
) -> None:
    pre_merge_model = "claude-opus-4-7" if tier_value != "post-deploy" else None
    post_deploy_model = "qwen-3.6" if tier_value != "pre-merge" else None
    md = _wrap_in_prompt(
        _qa_section(
            tier=tier_value,
            pre_merge_model=pre_merge_model,
            post_deploy_model=post_deploy_model,
        )
    )
    qa = parse_qa_test(md)
    assert qa.reviewer_tier == enum_value


# ── missing required fields ─────────────────────────────────────────────────


def test_missing_qa_test_section_raises() -> None:
    md = "# Task\n\nJust a description, no QA Test.\n"
    with pytest.raises(QATestParseError, match="missing a '## QA Test' section"):
        parse_qa_test(md)


def test_missing_type_field_raises() -> None:
    md = _wrap_in_prompt(
        _qa_section().replace("**Type:** browser\n", "")
    )
    with pytest.raises(QATestParseError, match="missing required field: 'Type'"):
        parse_qa_test(md)


def test_missing_reviewer_tier_raises() -> None:
    md = _wrap_in_prompt(
        _qa_section().replace("**Reviewer tier:** both\n", "")
    )
    with pytest.raises(QATestParseError, match="missing required field: 'Reviewer tier'"):
        parse_qa_test(md)


def test_invalid_type_value_raises() -> None:
    md = _wrap_in_prompt(_qa_section(type_="vibes-check"))
    with pytest.raises(QATestParseError, match="invalid value 'vibes-check'"):
        parse_qa_test(md)


def test_invalid_reviewer_tier_raises() -> None:
    md = _wrap_in_prompt(_qa_section(tier="post-merge"))
    with pytest.raises(QATestParseError, match="invalid value 'post-merge'"):
        parse_qa_test(md)


def test_missing_pre_merge_model_when_required_raises() -> None:
    md = _wrap_in_prompt(_qa_section(tier="pre-merge", pre_merge_model=None))
    with pytest.raises(QATestParseError, match="missing required field 'Pre-merge model'"):
        parse_qa_test(md)


def test_missing_post_deploy_model_when_required_raises() -> None:
    md = _wrap_in_prompt(_qa_section(tier="both", post_deploy_model=None))
    with pytest.raises(QATestParseError, match="missing required field 'Post-deploy model'"):
        parse_qa_test(md)


def test_pre_merge_model_must_be_hardcoded_value() -> None:
    md = _wrap_in_prompt(_qa_section(pre_merge_model="claude-sonnet-4-6"))
    with pytest.raises(
        QATestParseError, match="must be 'claude-opus-4-7'"
    ):
        parse_qa_test(md)


def test_post_deploy_model_must_be_hardcoded_value() -> None:
    md = _wrap_in_prompt(_qa_section(post_deploy_model="claude-haiku-4-5"))
    with pytest.raises(QATestParseError, match="must be 'qwen-3.6'"):
        parse_qa_test(md)


def test_browser_type_requires_browser_tool() -> None:
    md = _wrap_in_prompt(_qa_section(type_="browser", browser_tool=None))
    with pytest.raises(QATestParseError, match="must specify a 'Browser tool'"):
        parse_qa_test(md)


def test_browser_tool_must_be_in_allowlist() -> None:
    md = _wrap_in_prompt(_qa_section(browser_tool="puppeteer"))
    with pytest.raises(QATestParseError, match="'Browser tool' must be one of"):
        parse_qa_test(md)


# ── malformed steps ────────────────────────────────────────────────────────


def test_no_steps_raises() -> None:
    md = _wrap_in_prompt(_qa_section(steps=()))
    with pytest.raises(QATestParseError, match="at least one step"):
        parse_qa_test(md)


def test_non_sequential_step_numbers_raises() -> None:
    section = (
        "## QA Test\n\n"
        "**Type:** behavior\n"
        "**Reviewer tier:** pre-merge\n"
        "**Pre-merge model:** claude-opus-4-7\n"
        "**Pass criterion:** all assertions pass\n\n"
        "### Steps\n\n"
        "1. Run pytest.\n"
        "3. Assert exit code 0.\n"
    )
    md = _wrap_in_prompt(section)
    with pytest.raises(QATestParseError, match="sequentially numbered"):
        parse_qa_test(md)


# ── banned tools (planner bug) ──────────────────────────────────────────────


def test_clawd_browser_relay_in_steps_is_planner_bug() -> None:
    section = _qa_section(
        steps=(
            "Open `http://localhost:7777` via Clawd Browser Relay.",
            "Assert: page renders.",
        ),
    )
    md = _wrap_in_prompt(section)
    with pytest.raises(QATestParseError, match="banned tool"):
        parse_qa_test(md)


def test_headless_flag_in_browser_tool_is_planner_bug() -> None:
    # Browser tool field will fail allowlist check first; verify our top-level
    # ban catches headless mentions even if smuggled through other fields.
    section = _qa_section(
        steps=(
            "Open `http://localhost:7777` via `agent-browser --headless`.",
            "Assert: page renders.",
        ),
    )
    md = _wrap_in_prompt(section)
    with pytest.raises(QATestParseError, match="banned tool"):
        parse_qa_test(md)


# ── expected failures handling ─────────────────────────────────────────────


def test_expected_failures_optional() -> None:
    md = _wrap_in_prompt(_qa_section(expected_failures=()))
    qa = parse_qa_test(md)
    assert qa.expected_failures == ()


def test_first_qa_test_section_wins_when_multiple_present() -> None:
    duplicate = _qa_section() + "\n" + _qa_section(type_="behavior", browser_tool=None)
    md = _wrap_in_prompt(duplicate)
    qa = parse_qa_test(md)
    # First section is the browser one; second is ignored.
    assert qa.type == QATestType.BROWSER


# ── default pass_criterion ─────────────────────────────────────────────────


def test_pass_criterion_defaults_when_omitted() -> None:
    section = _qa_section()
    section_no_pc = "\n".join(
        line for line in section.splitlines() if not line.startswith("**Pass criterion:**")
    ) + "\n"
    md = _wrap_in_prompt(section_no_pc)
    qa = parse_qa_test(md)
    assert qa.pass_criterion == "all assertions pass"
