"""neumann.router — Deterministic persona-routing kernel for AI agent systems.

Sits between an LLM planner and downstream agent specialists. The LLM produces
structured plans; the router classifies each planned task and dispatches it to
the right persona.

LLM generates. Neumann routes.

Public API:

    from neumann.router import (
        ShapeClassifier,
        TaskTypeClassifier,
        ContextResolver,
        PersonaSelector,
        RoutingValidator,
        RoutingFallback,
        RouterPipeline,
        PlannedTask,
        Shape,
        TaskType,
        RoutingContext,
        PersonaDecision,
    )

Typical usage::

    pipeline = RouterPipeline()
    shape = pipeline.classify_shape(prompt="Fix the typo in README")
    if shape.shape == Shape.SINGLE_TASK:
        decision = pipeline.route(PlannedTask.from_prompt(prompt))
    else:
        plan = pipeline.plan(prompt)              # delegates to LLM planner
        decisions = [pipeline.route(t) for t in plan.tasks]
"""
from __future__ import annotations

from .types import (
    Shape,
    TaskType,
    PersonaId,
    RoutingContext,
    PlannedTask,
    Plan,
    ShapeDecision,
    PersonaDecision,
    RoutingTrace,
    ValidationResult,
    FALLBACK_SENTINEL,
    InterviewExchange,
    ConfirmedIntent,
)
from .shape_classifier import ShapeClassifier
from .task_classifier import TaskTypeClassifier
from .context_resolver import ContextResolver
from .persona_selector import PersonaSelector
from .validator import RoutingValidator
from .fallback import RoutingFallback
from .registry import PersonaRegistry, get_persona
from .planner_protocol import Planner, MockPlanner
from .decomposer import Decomposer
from .interviewer import (
    Interviewer,
    MockInterviewer,
    CLIInterviewer,
    ChatInterviewer,
    SlackInterviewer,
    LucidInterviewer,
    WebInterviewer,
    InterviewIncomplete,
    validate_intent,
)
from .pipeline import RouterPipeline, PipelineResult
from .qa_test import (
    QATest,
    QATestParseError,
    QATestType,
    ReviewerTier,
    parse_qa_test,
)
from .qa_retry import RetryAction, RetryPolicy, load_policy
from .qa_state import WatcherRecord, WatcherState
from .qa_executor import (
    ClaudeCliReviewer,
    QAExecutor,
    QAResult,
    QAStepResult,
    QATask,
    QAReviewer,
)
from .fusion_watcher import (
    ClawdbotWhatsAppNotifier,
    FusionClient,
    FusionTask,
    FusionWatcher,
    HttpFusionClient,
    WatcherStats,
    WhatsAppNotifier,
)

__all__ = [
    # types
    "Shape",
    "TaskType",
    "PersonaId",
    "RoutingContext",
    "PlannedTask",
    "Plan",
    "ShapeDecision",
    "PersonaDecision",
    "RoutingTrace",
    "ValidationResult",
    "FALLBACK_SENTINEL",
    "InterviewExchange",
    "ConfirmedIntent",
    # components
    "ShapeClassifier",
    "TaskTypeClassifier",
    "ContextResolver",
    "PersonaSelector",
    "RoutingValidator",
    "RoutingFallback",
    "PersonaRegistry",
    "get_persona",
    # pipeline
    "Planner",
    "MockPlanner",
    "Decomposer",
    "Interviewer",
    "MockInterviewer",
    "CLIInterviewer",
    "ChatInterviewer",
    "SlackInterviewer",
    "LucidInterviewer",
    "WebInterviewer",
    "InterviewIncomplete",
    "validate_intent",
    "RouterPipeline",
    "PipelineResult",
    # QA Test
    "QATest",
    "QATestParseError",
    "QATestType",
    "ReviewerTier",
    "parse_qa_test",
    # QA executor / retry / watcher
    "RetryAction",
    "RetryPolicy",
    "load_policy",
    "WatcherRecord",
    "WatcherState",
    "ClaudeCliReviewer",
    "QAExecutor",
    "QAResult",
    "QAStepResult",
    "QATask",
    "QAReviewer",
    "ClawdbotWhatsAppNotifier",
    "FusionClient",
    "FusionTask",
    "FusionWatcher",
    "HttpFusionClient",
    "WatcherStats",
    "WhatsAppNotifier",
]
