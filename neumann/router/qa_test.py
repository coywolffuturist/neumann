"""Parser for the ``## QA Test`` section of a task's PROMPT.md.

The Planner authors a structured QA Test at planning time (see
``personas/planner-system-prompt.md``). The pre-merge QA agent (Opus 4.7) and
the post-deploy Coywolf cron (Qwen 3.6) both consume the same parsed
representation. Keeping a single parser here is the contract bridge between
those two reviewers.

This module is parser-only. It does not execute QA Tests, dispatch agents, or
run browsers. It validates the structure and rejects planner bugs (banned
tools, missing required fields, malformed steps) so downstream executors
operate on well-formed input.

Schema reference: ``coywolffuturist/neumann:docs/specs/qa-agent.md``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class QATestType(str, Enum):
    BROWSER = "browser"
    BEHAVIOR = "behavior"
    REFACTOR_EQUIVALENCE = "refactor-equivalence"
    COPY_REVIEW = "copy-review"
    RESEARCH_SOUNDNESS = "research-soundness"


class ReviewerTier(str, Enum):
    PRE_MERGE = "pre-merge"
    POST_DEPLOY = "post-deploy"
    BOTH = "both"


# Hardcoded model assignments per spec (qa-agent.md → "Two verification tiers").
# These are the only legal values the planner may emit. Any deviation = planner bug.
PRE_MERGE_MODEL = "claude-opus-4-7"
POST_DEPLOY_MODEL = "qwen-3.6"

# Allowed browser tools when ``type == BROWSER``. ``agent-browser --show`` is the
# default; ``cuadriver`` is the escalation path. Headless and Clawd Browser
# Relay are hard-banned.
ALLOWED_BROWSER_TOOLS = ("agent-browser --show", "cuadriver")

# Substrings that, if present in any field value, indicate a planner bug.
# See ``feedback_clawd_browser_relay.md`` and the QA agent persona's tool_blocklist.
BANNED_BROWSER_SUBSTRINGS = (
    "clawd browser relay",
    "clawd-browser-relay",
    "clawd_browser_relay",
    "--headless",
    "headless=true",
)


class QATestParseError(ValueError):
    """Raised when PROMPT.md's ``## QA Test`` section is missing, malformed,
    or names a banned tool. The QA agent must surface this as
    ``verdict=PLANNER_BUG`` rather than attempt to execute.
    """


@dataclass(frozen=True)
class QATest:
    """Structured representation of a ``## QA Test`` section."""

    type: QATestType
    reviewer_tier: ReviewerTier
    pre_merge_model: str | None
    post_deploy_model: str | None
    pass_criterion: str
    browser_tool: str | None
    steps: tuple[str, ...]
    expected_failures: tuple[str, ...] = field(default_factory=tuple)

    @property
    def runs_pre_merge(self) -> bool:
        return self.reviewer_tier in (ReviewerTier.PRE_MERGE, ReviewerTier.BOTH)

    @property
    def runs_post_deploy(self) -> bool:
        return self.reviewer_tier in (ReviewerTier.POST_DEPLOY, ReviewerTier.BOTH)


# ── public API ─────────────────────────────────────────────────────────────


def parse_qa_test(prompt_md: str) -> QATest:
    """Parse the first ``## QA Test`` section in ``prompt_md``.

    Raises ``QATestParseError`` on missing section, missing required fields,
    malformed steps, or banned tool references.
    """
    section = _extract_section(prompt_md)
    fields = _parse_fields(section)
    steps = _parse_steps(section)
    expected_failures = _parse_expected_failures(section)

    qa_type = _required_enum(fields, "Type", QATestType)
    reviewer_tier = _required_enum(fields, "Reviewer tier", ReviewerTier)
    pass_criterion = fields.get("Pass criterion", "all assertions pass").strip()

    pre_merge_model = _model_for_tier(
        fields, key="Pre-merge model", expected=PRE_MERGE_MODEL,
        required=reviewer_tier in (ReviewerTier.PRE_MERGE, ReviewerTier.BOTH),
    )
    post_deploy_model = _model_for_tier(
        fields, key="Post-deploy model", expected=POST_DEPLOY_MODEL,
        required=reviewer_tier in (ReviewerTier.POST_DEPLOY, ReviewerTier.BOTH),
    )

    browser_tool = _browser_tool_for_type(fields, qa_type)

    if not steps:
        raise QATestParseError("QA Test must contain at least one step under '### Steps'")

    _enforce_no_banned_tools(fields, steps)

    return QATest(
        type=qa_type,
        reviewer_tier=reviewer_tier,
        pre_merge_model=pre_merge_model,
        post_deploy_model=post_deploy_model,
        pass_criterion=pass_criterion,
        browser_tool=browser_tool,
        steps=tuple(steps),
        expected_failures=tuple(expected_failures),
    )


# ── extraction ─────────────────────────────────────────────────────────────


_QA_HEADING_RE = re.compile(r"^##\s+QA\s+Test\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_LEVEL2_HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)


def _extract_section(prompt_md: str) -> str:
    match = _QA_HEADING_RE.search(prompt_md)
    if not match:
        raise QATestParseError("PROMPT.md is missing a '## QA Test' section")

    body_start = match.end()
    rest = prompt_md[body_start:]
    next_heading = _NEXT_LEVEL2_HEADING_RE.search(rest)
    if next_heading:
        return rest[: next_heading.start()]
    return rest


# ── field parsing ──────────────────────────────────────────────────────────


# Matches lines like ``**Type:** browser`` — bold key, colon, value to EOL.
_FIELD_LINE_RE = re.compile(r"^\*\*(?P<key>[^*]+?)\:\*\*\s*(?P<value>.*)$", re.MULTILINE)


def _parse_fields(section: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    # Field lines only count BEFORE the first ``###`` subheading (Steps,
    # Expected failure modes). Anything inside step bodies is not a field.
    head, _, _ = section.partition("\n### ")
    for m in _FIELD_LINE_RE.finditer(head):
        key = m.group("key").strip()
        value = m.group("value").strip()
        fields[key] = value
    return fields


def _required_enum(fields: dict[str, str], key: str, enum_cls: type[Enum]) -> Enum:
    if key not in fields:
        raise QATestParseError(f"QA Test missing required field: '{key}'")
    raw = fields[key]
    try:
        return enum_cls(raw)
    except ValueError as e:
        valid = ", ".join(repr(v.value) for v in enum_cls)
        raise QATestParseError(
            f"QA Test field '{key}' has invalid value {raw!r}; expected one of: {valid}"
        ) from e


def _model_for_tier(
    fields: dict[str, str], *, key: str, expected: str, required: bool,
) -> str | None:
    if key not in fields:
        if required:
            raise QATestParseError(
                f"QA Test missing required field '{key}' for the declared Reviewer tier"
            )
        return None
    raw = fields[key].strip()
    if raw != expected:
        raise QATestParseError(
            f"QA Test field '{key}' must be {expected!r} (hardcoded per spec); got {raw!r}"
        )
    return raw


def _browser_tool_for_type(fields: dict[str, str], qa_type: QATestType) -> str | None:
    raw = fields.get("Browser tool", "").strip()
    if qa_type != QATestType.BROWSER:
        # Browser tool is irrelevant for non-browser tests; ignore if present.
        return raw or None
    if not raw:
        raise QATestParseError(
            "QA Test of Type 'browser' must specify a 'Browser tool'"
        )
    if raw not in ALLOWED_BROWSER_TOOLS:
        raise QATestParseError(
            f"QA Test 'Browser tool' must be one of {ALLOWED_BROWSER_TOOLS}; got {raw!r}"
        )
    return raw


# ── step parsing ───────────────────────────────────────────────────────────


_STEP_LINE_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_STEPS_SUBHEADING_RE = re.compile(r"^###\s+Steps\s*$", re.IGNORECASE | re.MULTILINE)
_EXPECTED_SUBHEADING_RE = re.compile(
    r"^###\s+Expected failure modes\s*$", re.IGNORECASE | re.MULTILINE
)


def _parse_steps(section: str) -> list[str]:
    head_match = _STEPS_SUBHEADING_RE.search(section)
    if not head_match:
        return []
    body = section[head_match.end():]
    next_sub = re.search(r"^###\s+", body, re.MULTILINE)
    if next_sub:
        body = body[: next_sub.start()]

    steps: list[str] = []
    last_n = 0
    for m in _STEP_LINE_RE.finditer(body):
        n = int(m.group(1))
        text = m.group(2).strip()
        if not text:
            raise QATestParseError(f"QA Test step #{n} has empty body")
        if n != last_n + 1:
            raise QATestParseError(
                f"QA Test steps must be sequentially numbered; got step {n} after {last_n}"
            )
        last_n = n
        steps.append(text)
    return steps


def _parse_expected_failures(section: str) -> list[str]:
    head_match = _EXPECTED_SUBHEADING_RE.search(section)
    if not head_match:
        return []
    body = section[head_match.end():]
    next_sub = re.search(r"^###\s+", body, re.MULTILINE)
    if next_sub:
        body = body[: next_sub.start()]

    items: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


# ── safety ─────────────────────────────────────────────────────────────────


def _enforce_no_banned_tools(fields: dict[str, str], steps: list[str]) -> None:
    haystacks = list(fields.values()) + list(steps)
    for hs in haystacks:
        low = hs.lower()
        for banned in BANNED_BROWSER_SUBSTRINGS:
            if banned in low:
                raise QATestParseError(
                    f"QA Test references banned tool/mode {banned!r} — "
                    "Clawd Browser Relay and headless Chrome are hard-banned. "
                    "See feedback_clawd_browser_relay.md and qa.json tool_blocklist."
                )
