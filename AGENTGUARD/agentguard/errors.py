"""Agent-facing exceptions that drive Phase 2 decision transitions.

An agent function signals a non-CONTINUE outcome by raising one of
these from inside its own code (or by AgentGuard's synchronous policy
checks raising one on its behalf, e.g. a forbidden action). `_execute`
(decorator.py) catches these specifically — they are the "boundary
where the agent can receive a re-planning signal" and friends that the
Phase 2 spec asks for, made concrete as real control flow rather than a
label attached after the fact.
"""
from __future__ import annotations

from typing import Any


class TransientError(Exception):
    """Raised by agent code for a failure the caller should just retry.

    Caught by the Decision Engine's RETRY path (decorator.py's execution
    loop), bounded by Policy.retry_limit.
    """

    def __init__(self, message: str = "transient failure", **evidence: Any) -> None:
        super().__init__(message)
        self.evidence = evidence


class ReplanRequested(Exception):
    """Raised by agent code (or synchronous policy checks) when the
    current plan should not continue unchanged.

    Carries a structured `reason` and optional `context` that is handed
    back to the agent function on its next invocation via
    `agentguard.get_replan_context()`.
    """

    def __init__(self, reason: str = "replan requested", *, context: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.context = context or {}


class ForbiddenActionError(Exception):
    """Raised synchronously the moment an agent performs an action listed
    in Policy.forbidden_actions. Never delayed to post-hoc evaluation —
    this is a synchronous, local, deterministic guardrail (Rule 15)."""

    def __init__(self, action: str) -> None:
        super().__init__(f"action {action!r} is forbidden by policy")
        self.action = action


class HumanRejected(Exception):
    """Raised when a human reviewer rejects a pending approval request
    (or the request times out and the policy's timeout behavior is
    'deny', which is the only Phase 2 timeout behavior)."""

    def __init__(self, action: str, reason: str = "") -> None:
        super().__init__(f"human rejected action {action!r}: {reason}")
        self.action = action
        self.reason = reason


class HumanReplanRequested(ReplanRequested):
    """A human reviewer asked for a re-plan instead of approving/rejecting."""

    def __init__(self, action: str, reason: str = "") -> None:
        super().__init__(reason or f"human requested replan for action {action!r}", context={"action": action})
        self.action = action


class LLMGatewayViolation(Exception):
    """Raised synchronously the moment a governed LLM call violates
    Policy.llm_gateway (disallowed model, or a request-side
    max_tokens_per_call ceiling) — same synchronous, local,
    deterministic guardrail precedent as ForbiddenActionError."""

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"LLM gateway policy violated ({rule}): {detail}")
        self.rule = rule
        self.detail = detail


class CircuitBreakerTripped(Exception):
    """Raised synchronously by agentguard.call_tool() the moment a run
    crosses a Policy.circuit_breaker threshold (too many consecutive
    tool-call failures across any tool, or too many loop iterations) —
    same synchronous, local, deterministic guardrail precedent as
    ForbiddenActionError/LLMGatewayViolation.

    `code_file`/`code_function`/`code_lineno` name the exact line in the
    AGENT'S OWN code that called call_tool() when the threshold was
    crossed (captured via agentguard.tracing.recording._find_call_site,
    the same call-site-walking helper TraceStep recording already uses)
    — this is what lets a halted run's audit trail point at a real node
    instead of just a rule name."""

    def __init__(
        self,
        rule: str,
        detail: str,
        *,
        tool: str | None = None,
        code_file: str | None = None,
        code_function: str | None = None,
        code_lineno: int | None = None,
    ) -> None:
        super().__init__(f"circuit breaker tripped ({rule}): {detail}")
        self.rule = rule
        self.detail = detail
        self.tool = tool
        self.code_file = code_file
        self.code_function = code_function
        self.code_lineno = code_lineno
