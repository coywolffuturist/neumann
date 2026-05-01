"""Decomposer — split oversized intents into sub-intents before planning.

Sits between the Interviewer (LLM translation) and the Planner. When a
``ConfirmedIntent`` exceeds the complexity thresholds in
``rules/decomposition_rules.json``, the Decomposer fans it out into N
sub-intents (one per declared success criterion, or one per inferred seam
when criteria are absent) plus one integration sub-intent that depends on
every child. The Planner then runs ONCE PER SUB-INTENT, so each Planner
invocation only ever sees one focused area.

This is Brendan's column-mapping ordering:

    Prompt → LLM translation → Decomposer → Planner → Todo (router) →
    In Progress (executor) → In Review (QA).

Why intent-level (not task-level) decomposition: the Planner's job is to
turn ONE intent into a tight spec. If the Planner accepts a sprawling
mission, every spec becomes overlong and the executor falls into the
FN-009 trap (sub-agent delegation, infinite worktree thrash). Splitting
at the intent boundary keeps the Planner's input small and the output
uniformly crisp — 1 intent in → 1 (or a few) tasks out.

Pure function over the ConfirmedIntent. No I/O beyond the rules JSON
read at init.

See ``docs/specs/pipeline-ordering.md`` for the full design.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .types import ConfirmedIntent

DEFAULT_RULES_PATH = Path(__file__).parent / "rules" / "decomposition_rules.json"

# Default thresholds applied when the rules file is missing or empty.
# Rules-file keys mirror these names exactly.
_DEFAULT_THRESHOLDS = {
    "max_lines_estimate": 500,
    "max_files_changed": 3,
    "max_distinct_outputs": 2,
}

# Verbs that count as "distinct outputs" when found in intent prose.
# Rough heuristic — refined as we see real interviewer output.
_OUTPUT_VERB_RE = re.compile(
    r"\b(create|add|implement|build|generate|write|extract|introduce|refactor|wire)\b",
    re.IGNORECASE,
)

# Recognize file paths mentioned in prose. Catches `app/server.js`,
# `routes/intel.js`, bare `dashboard.html`, etc. Conservative — we'd rather
# undercount than over-flag prose that mentions filename-like words.
_FILE_PATH_RE = re.compile(
    r"\b[\w][\w./-]*\.(?:py|js|ts|tsx|jsx|mjs|cjs|html|css|scss|sql|json|yaml|yml|md|sh|toml)\b",
    re.IGNORECASE,
)

# Rough conversion: 50 description characters ≈ 1 LOC of resulting code.
# Pessimistic — better to over-flag than under-flag (false positives just
# create more micro-intents; false negatives recreate the FN-009 thrash).
_CHARS_PER_LINE_ESTIMATE = 50


class Decomposer:
    """Splits oversized ConfirmedIntents into sub-intents + integration intent.

    Construct with no args to use the bundled rules JSON, or pass a custom
    ``rules_path`` / inline ``thresholds`` dict for tests.
    """

    def __init__(
        self,
        rules_path: Path | str | None = None,
        thresholds: dict[str, int] | None = None,
    ) -> None:
        if thresholds is not None:
            self._thresholds = {**_DEFAULT_THRESHOLDS, **thresholds}
        else:
            self._thresholds = self._load_rules(
                Path(rules_path) if rules_path else DEFAULT_RULES_PATH
            )

    # ── public surface ────────────────────────────────────────

    def decompose(self, intent: ConfirmedIntent) -> list[ConfirmedIntent]:
        """Return a list of sub-intents.

        For intents that fit under threshold, returns ``[intent]`` (single
        element — the Planner runs once on the original intent).

        For oversized intents, returns ``[child_1, ..., child_N, integration]``.
        Each child is a focused sub-intent; the integration intent records
        its dependencies on the children via ``extra["depends_on_sub_intents"]``.
        """
        if not self._exceeds_threshold(intent):
            return [intent]
        return self._split(intent)

    # ── internals ─────────────────────────────────────────────

    def _exceeds_threshold(self, intent: ConfirmedIntent) -> bool:
        prose = self._collect_prose(intent)

        # Hard signal: explicit success_criteria list with too many items.
        if len(intent.success_criteria) > self._thresholds["max_distinct_outputs"]:
            return True

        # Soft signal: distinct output verbs in the prose.
        if self._count_distinct_outputs(prose) > self._thresholds["max_distinct_outputs"]:
            return True

        # File-mention signal: prose names too many distinct files.
        if self._count_distinct_files(prose) > self._thresholds["max_files_changed"]:
            return True

        # Volume signal: prose is just too long to spec as one task.
        if self._estimate_lines(prose) > self._thresholds["max_lines_estimate"]:
            return True

        return False

    def _split(self, intent: ConfirmedIntent) -> list[ConfirmedIntent]:
        """Fan one oversized intent into N children + 1 integration intent."""
        parent_id = self._intent_id(intent.confirmed_intent)

        # Prefer to split along the user's explicit success_criteria when
        # they declared them. Otherwise fall back to inferred seams from
        # bullets / verb-led sentences / file mentions.
        if intent.success_criteria:
            seams = list(intent.success_criteria)
        else:
            seams = self._infer_seams(self._collect_prose(intent))

        if not seams:
            # Pathological case — exceeded threshold but no clean seams to
            # split along. Return as-is to avoid producing junk sub-intents.
            return [intent]

        children: list[ConfirmedIntent] = []
        for idx, seam in enumerate(seams):
            child_id = f"{parent_id}-c{idx}"
            child_focus = (
                f"{intent.confirmed_intent}\n\nFocus for this sub-intent: {seam}"
            )
            children.append(
                ConfirmedIntent(
                    raw_prompt=intent.raw_prompt,
                    confirmed_intent=child_focus,
                    target_repo=intent.target_repo,
                    success_criteria=(seam,),
                    constraints=intent.constraints,
                    out_of_scope=intent.out_of_scope,
                    human_approver_id=intent.human_approver_id,
                    human_approved=intent.human_approved,
                    transcript=intent.transcript,
                    extra={
                        **intent.extra,
                        "parent_intent_id": parent_id,
                        "sub_intent_id": child_id,
                        "decomposed": True,
                    },
                )
            )

        integration_id = f"{parent_id}-int"
        integration_focus = (
            f"Integration step for the decomposed parent intent: "
            f"{intent.confirmed_intent[:200]}\n\n"
            f"Wire up the {len(children)} child sub-intents into a coherent "
            "whole. Run after every child has produced its output."
        )
        integration = ConfirmedIntent(
            raw_prompt=intent.raw_prompt,
            confirmed_intent=integration_focus,
            target_repo=intent.target_repo,
            success_criteria=("All child outputs are integrated cleanly.",),
            constraints=intent.constraints,
            out_of_scope=intent.out_of_scope,
            human_approver_id=intent.human_approver_id,
            human_approved=intent.human_approved,
            transcript=intent.transcript,
            extra={
                **intent.extra,
                "parent_intent_id": parent_id,
                "sub_intent_id": integration_id,
                "decomposed": True,
                "is_integration": True,
                "depends_on_sub_intents": tuple(
                    c.extra["sub_intent_id"] for c in children
                ),
            },
        )

        return [*children, integration]

    @staticmethod
    def _collect_prose(intent: ConfirmedIntent) -> str:
        """Concatenate intent fields the heuristics scan as one blob."""
        parts: list[str] = [intent.confirmed_intent]
        parts.extend(intent.success_criteria)
        parts.extend(intent.constraints)
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _count_distinct_outputs(prose: str) -> int:
        return len(_OUTPUT_VERB_RE.findall(prose))

    @staticmethod
    def _count_distinct_files(prose: str) -> int:
        return len({m.lower() for m in _FILE_PATH_RE.findall(prose)})

    @staticmethod
    def _estimate_lines(prose: str) -> int:
        return len(prose) // _CHARS_PER_LINE_ESTIMATE

    @staticmethod
    def _intent_id(text: str) -> str:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"di-{h[:10]}"

    @staticmethod
    def _infer_seams(prose: str) -> list[str]:
        """Best-effort split into sub-intent seams when criteria are absent.

        Tries (in order):
          1. Bullet items at the top of the prose
          2. Sentences that start with an output verb
          3. Distinct file mentions, each becomes "work on <file>"
        """
        lines = [ln.strip() for ln in prose.splitlines() if ln.strip()]
        bullets = [
            ln.lstrip("-*• ").strip()
            for ln in lines
            if ln.startswith(("-", "*", "•"))
        ]
        if len(bullets) >= 2:
            return bullets

        verb_seams: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", prose):
            sentence = sentence.strip()
            if not sentence:
                continue
            if _OUTPUT_VERB_RE.search(sentence):
                verb_seams.append(sentence)
        if len(verb_seams) >= 2:
            return verb_seams

        files = sorted({m for m in _FILE_PATH_RE.findall(prose)})
        if len(files) >= 2:
            return [f"Work on {f}" for f in files]

        return []

    @staticmethod
    def _load_rules(path: Path) -> dict[str, int]:
        """Read thresholds from JSON. Missing/malformed file → defaults."""
        if not path.exists():
            return dict(_DEFAULT_THRESHOLDS)
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return dict(_DEFAULT_THRESHOLDS)
        if not isinstance(raw, dict):
            return dict(_DEFAULT_THRESHOLDS)
        # Coerce only the recognized keys; ignore unknown keys silently
        # (forward-compat with rule additions in newer files).
        merged = dict(_DEFAULT_THRESHOLDS)
        for key in _DEFAULT_THRESHOLDS:
            value = raw.get(key)
            if isinstance(value, int) and value > 0:
                merged[key] = value
        return merged
