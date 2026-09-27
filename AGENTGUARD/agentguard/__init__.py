"""AgentGuard — a low-code Python SDK for autonomous AI agent reliability.

Phase 1 shipped the core loop: the @monitor decorator, OpenTelemetry
span capture, PostgreSQL persistence, a deterministic Constraint
Adherence evaluator, and a CONTINUE/STOP Decision Engine.

Phase 2 (this release) adds RETRY/REPLAN/HUMAN, a declarative Policy
engine, the causal state-diff Root-Cause Engine, dynamic per-action Risk
Scoring, confidence/uncertainty routing, an asynchronous LLM-as-Judge
evaluator, and a WebSocket human-approval channel. See
docs/EXECUTION_REPORT_PHASE_2.md for what was actually implemented and
verified, and docs/PHASE1.md / this module's docstrings for what remains
deferred to Phase 3/4.
"""
from . import errors
from ._runtime import configure
from .client import AgentGuard, AuthenticationError
from .context import (
    ActionResult,
    get_replan_context,
    get_state,
    perform_action,
    perform_action_with_result,
    record_tokens,
    request_approval,
    reset_state,
    update_state,
)
from .decorator import monitor, wait_for_background_tasks
from .models import (
    AgentState,
    CircuitBreakerPolicy,
    Decision,
    EvalResult,
    HumanDecision,
    LLMGatewayPolicy,
    ModelAlternative,
    Policy,
    PolicyFinding,
    RiskAssessment,
    RootCause,
    Run,
    RunStatus,
    StateSnapshot,
    TraceStep,
)
from .tracing import traceable, traced_acompletion, traced_completion, wrap_llm_client

__all__ = [
    "AgentGuard",
    "AuthenticationError",
    "monitor",
    "Policy",
    "PolicyFinding",
    "AgentState",
    "Run",
    "RunStatus",
    "EvalResult",
    "Decision",
    "RiskAssessment",
    "RootCause",
    "HumanDecision",
    "StateSnapshot",
    "TraceStep",
    "LLMGatewayPolicy",
    "ModelAlternative",
    "CircuitBreakerPolicy",
    "traceable",
    "wrap_llm_client",
    "traced_completion",
    "traced_acompletion",
    "get_state",
    "update_state",
    "reset_state",
    "record_tokens",
    "get_replan_context",
    "perform_action",
    "perform_action_with_result",
    "ActionResult",
    "request_approval",
    "configure",
    "wait_for_background_tasks",
    "errors",
]

__version__ = "0.2.0"
