"""Ambient run context, propagated via contextvars.

Lets agent code report state (via `update_state`) and perform
policy-gated actions (via `perform_action`) without the developer
threading a Run object through every function call — this is what
keeps @monitor low-code (Rule 1).

Phase 2 additions:
- `state_history`: an ordered `StateSnapshot` list, one entry per
  `update_state()` call (plus the seeded initial state) — this is what
  the Root-Cause Engine diffs.
- `perform_action()` / `request_approval()`: the synchronous-then-async
  boundary an agent crosses to declare a real action. Forbidden actions
  raise immediately and locally (no I/O); actions requiring approval
  block (with a deterministic timeout) on a live human decision routed
  through the WebSocket approval channel.
- `get_replan_context()`: what a REPLAN transition hands back to the
  agent function on its next invocation.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

from .errors import HumanRejected, HumanReplanRequested
from .models import AgentState, HumanDecision, PolicyFinding, RiskAssessment, Run, RunStatus, StateSnapshot


@dataclass
class RunContext:
    run: Run
    state: AgentState = field(default_factory=AgentState)
    state_history: list[StateSnapshot] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    replan_context: dict[str, Any] | None = None
    retry_count: int = 0
    replan_count: int = 0
    consecutive_tool_failures: int = 0
    """Circuit breaker (Policy.circuit_breaker): cross-tool count of
    consecutive failure/timeout ToolCallEvents, reset to 0 by any tool's
    success. Compared against max_consecutive_tool_failures in
    call_tool()."""
    loop_iterations: int = 0
    """Circuit breaker: number of call_tool() invocations so far this
    run, compared against max_loop_iterations at the top of call_tool()."""
    audit_log: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """Phase 3: ordered (event_type, payload) pairs accumulated as the
    run executes. Turned into a real SHA-256 hash chain and persisted in
    one batch at the end of the run — see decorator.py and
    agentguard/audit/chain.py."""

    _seen_tool_calls: set[tuple[str, str]] = field(default_factory=set)
    """Phase 4: (tool, canonical-args) pairs already seen this run, for
    duplicate-call detection in call_tool()."""

    def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.audit_log.append((event_type, payload))


_current: "contextvars.ContextVar[RunContext | None]" = contextvars.ContextVar(
    "agentguard_current_run", default=None
)


def _set_current(ctx: RunContext | None) -> contextvars.Token:
    return _current.set(ctx)


def _reset_current(token: contextvars.Token) -> None:
    _current.reset(token)


def current_run() -> RunContext | None:
    """Return the ambient run context, or None outside a monitored call."""
    return _current.get()


def _require_context() -> RunContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("called outside a @monitor-wrapped run")
    return ctx


def get_state() -> AgentState:
    """Return the mutable AgentState for the run currently executing.

    Raises RuntimeError if called outside a function wrapped by @monitor.
    """
    return _require_context().state


def _record_snapshot(ctx: RunContext) -> StateSnapshot:
    seq = len(ctx.state_history) + 1
    snapshot = StateSnapshot(label=f"S{seq}", seq=seq, data=ctx.state.snapshot())
    ctx.state_history.append(snapshot)
    return snapshot


def seed_initial_snapshot(ctx: RunContext) -> None:
    """Called once by decorator.py right after the run's initial state is
    seeded, so S1 always exists even if the agent never calls
    update_state() again."""
    _record_snapshot(ctx)


def update_state(**kwargs: Any) -> None:
    """get_state().update(**kwargs), plus recording a new StateSnapshot
    (S2, S3, ...) for the Root-Cause Engine to diff against."""
    ctx = _require_context()
    ctx.state.update(**kwargs)
    _record_snapshot(ctx)


def reset_state(**kwargs: Any) -> None:
    """Replace the run's entire state with a fresh one (only the given
    kwargs survive). Models a real failure mode — an agent's working
    state getting silently clobbered or truncated between steps (a
    context-window trim, a buggy summarization pass) — without reaching
    into AgentState's internals. This is how a previously-set constraint
    can legitimately "disappear" from state, which is exactly what the
    Root-Cause Engine is built to catch. Records a new StateSnapshot like
    update_state() does."""
    ctx = _require_context()
    ctx.state = AgentState(**kwargs)
    _record_snapshot(ctx)


def record_tokens(
    *,
    model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> None:
    """Report real LLM token/cost usage for the current run (Dashboard
    V2's Tokens & Cost page). Only call this with numbers you actually
    have — there is no default/estimate here; a run that never calls
    this simply has no token data, and the dashboard shows "N/A" for it
    rather than a fabricated figure."""
    ctx = _require_context()
    if model_name is not None:
        ctx.run.model_name = model_name
    if input_tokens is not None:
        ctx.run.tokens_input = (ctx.run.tokens_input or 0) + input_tokens
    if output_tokens is not None:
        ctx.run.tokens_output = (ctx.run.tokens_output or 0) + output_tokens
    if cost_usd is not None:
        ctx.run.estimated_cost_usd = (ctx.run.estimated_cost_usd or 0.0) + cost_usd


def get_replan_context() -> dict[str, Any]:
    """What the most recent REPLAN transition handed back to the agent.
    Empty on a run's first attempt."""
    ctx = _current.get()
    return dict(ctx.replan_context) if ctx and ctx.replan_context else {}


async def request_approval(
    action: str,
    *,
    finding: PolicyFinding | None = None,
    reason: str = "",
    evidence: dict[str, Any] | None = None,
) -> HumanDecision:
    """Low-level primitive: publish a pending approval request over the
    WebSocket channel and the REST-resolvable broker, then await (with a
    deterministic, policy-configured timeout) a human's response.

    Raises HumanRejected on REJECT or on timeout (Rule: "on timeout:
    deny" — the agent never hangs forever). Raises HumanReplanRequested
    if the reviewer asks for a re-plan instead. Returns the resolved
    HumanDecision on APPROVE.

    Most callers should use `perform_action()` instead, which also runs
    the synchronous forbidden/require_approval policy check first.
    """
    ctx = _require_context()
    from ._runtime import get_repository
    from .human.broker import get_broker
    from .reliability.risk import RiskEngine

    risk = RiskEngine().assess(
        ctx.run.id,
        eval_results=[],
        policy_findings=[finding] if finding else [],
        action=action,
    )

    request = HumanDecision(
        run_id=ctx.run.id,
        action=action,
        decision=RunStatus.HUMAN,
        risk_score=risk.risk_score,
        confidence=risk.confidence,
        reason=reason or (finding.detail if finding else f"action {action!r} requires human approval"),
        evidence=evidence or (finding.model_dump() if finding else {}),
        timeout_s=ctx.run.policy.human_timeout_s,
    )

    repository = get_repository()
    await repository.save_human_decision(request)
    ctx.record_event(
        "HUMAN",
        {"phase": "requested", "action": action, "human_decision_id": request.id, "reason": request.reason},
    )

    resolved = await get_broker().request(request)
    await repository.save_human_decision(resolved)
    ctx.record_event(
        "HUMAN",
        {
            "phase": "resolved",
            "action": action,
            "human_decision_id": resolved.id,
            "status": resolved.status,
            "resolved_by": resolved.resolved_by,
        },
    )

    ctx.actions.append({"action": action, "human_decision": resolved.model_dump(mode="json")})

    if resolved.status == "approved":
        return resolved
    if resolved.status == "replan":
        raise HumanReplanRequested(action, resolved.reason)
    raise HumanRejected(action, resolved.reason or f"status={resolved.status}")


async def perform_action(action: str, **evidence: Any) -> PolicyFinding | None:
    """Declare that the agent is about to take `action`.

    1. Runs the synchronous, local, deterministic policy guardrail
       (Policy.forbidden_actions) — raises ForbiddenActionError
       immediately if forbidden, with no I/O involved.
    2. If the action is listed in Policy.require_approval, blocks (async,
       with a deterministic timeout) on a live human decision via
       `request_approval()`.

    Returns the PolicyFinding produced by the synchronous check (None if
    the action is unrestricted).
    """
    ctx = _require_context()
    from .errors import ForbiddenActionError
    from .policy.engine import PolicyEngine

    try:
        finding = PolicyEngine().check_action(ctx.run.policy, action)
    except ForbiddenActionError:
        ctx.record_event("POLICY_CHECK", {"action": action, "violated": True, "rule": "forbidden_actions", "detail": f"action {action!r} is forbidden"})
        raise
    ctx.record_event(
        "POLICY_CHECK",
        {
            "action": action,
            "violated": finding.violated if finding else False,
            "rule": finding.rule if finding else None,
            "detail": finding.detail if finding else "",
        },
    )
    ctx.actions.append({"action": action, "evidence": evidence, "requires_approval": finding is not None})

    if finding is not None:
        await request_approval(action, finding=finding, evidence=evidence)

    return finding


async def _invoke_tool(fn: Any, args: tuple, kwargs: dict) -> Any:
    import asyncio
    import inspect

    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def call_tool(
    tool_name: str,
    *args: Any,
    primary_attempts: int = 1,
    timeout_s: float | None = None,
    **kwargs: Any,
) -> Any:
    """Invoke `tool_name` (Phase 4) through the registered
    `agentguard.tools.ToolRegistry`, recording one `ToolCallEvent` per
    attempt (success/failure/timeout, latency, duplicate/fallback flags)
    — the raw evidence `agentguard.reliability.tool_profile` aggregates
    into a `ToolProfile`.

    Flow on primary failure (after `primary_attempts` tries):
      tool failure -> consult the tool's historical ToolProfile ->
      if a fallback is registered AND (insufficient history OR the
      profile's success_rate is below its configured
      reliability_threshold) -> try the fallback once -> on success,
      return normally (a TOOL_SUBSTITUTED audit event + reliability-report
      intervention record it) -> on failure, or if no fallback is
      registered/reliable, raise ReplanRequested (or escalate to HUMAN,
      per `policy.on_tool_exhausted`) so the existing REPLAN/HUMAN
      machinery in decorator.py handles it — this function never
      invents a new control-flow path.

    Circuit breaker (Policy.circuit_breaker, optional — None means none
    of this applies, unchanged behavior): `max_loop_iterations` is
    checked right here, before the tool is even attempted, incrementing
    a per-run counter of call_tool() invocations. `max_consecutive_tool_failures`
    is checked inside `_attempt()`, immediately after any failure/timeout
    is recorded — a run-level, cross-tool ceiling that takes priority
    over the fallback/replan recovery below (a hard stop, not a softer
    per-tool retry). Either breach raises CircuitBreakerTripped with the
    real agent code location that made the call, bypassing this
    function's own exception handling entirely (see the `except
    CircuitBreakerTripped: raise` guards below) so it always reaches
    decorator.py unchanged.
    """
    import asyncio
    import time

    from .errors import CircuitBreakerTripped, ReplanRequested
    from .models import ToolCallEvent
    from .reliability.tool_profile import ToolProfileEngine
    from .tools.registry import get_registry
    from .tracing.recording import _find_call_site

    ctx = _require_context()
    from ._runtime import get_repository

    repository = get_repository()
    registry = get_registry()
    policy = ctx.run.policy
    breaker = policy.circuit_breaker

    if breaker is not None:
        ctx.loop_iterations += 1
        if breaker.max_loop_iterations is not None and ctx.loop_iterations > breaker.max_loop_iterations:
            code_file, code_function, code_lineno = _find_call_site()
            raise CircuitBreakerTripped(
                "max_loop_iterations",
                f"{ctx.loop_iterations} call_tool() invocations exceeds policy limit of "
                f"{breaker.max_loop_iterations} for this run",
                tool=tool_name,
                code_file=code_file, code_function=code_function, code_lineno=code_lineno,
            )

    args_key = repr((args, sorted(kwargs.items())))
    seen_key = (tool_name, args_key)
    duplicate = seen_key in ctx._seen_tool_calls
    ctx._seen_tool_calls.add(seen_key)

    def _check_consecutive_failures(name: str) -> None:
        if breaker is None or breaker.max_consecutive_tool_failures is None:
            return
        if ctx.consecutive_tool_failures >= breaker.max_consecutive_tool_failures:
            code_file, code_function, code_lineno = _find_call_site()
            raise CircuitBreakerTripped(
                "max_consecutive_tool_failures",
                f"{ctx.consecutive_tool_failures} consecutive tool-call failures reaches policy "
                f"limit of {breaker.max_consecutive_tool_failures}",
                tool=name,
                code_file=code_file, code_function=code_function, code_lineno=code_lineno,
            )

    async def _attempt(name: str, attempt: int, is_fallback: bool) -> Any:
        fn = registry.get_callable(name)
        if fn is None:
            raise RuntimeError(f"agentguard.call_tool: tool {name!r} is not registered")
        t0 = time.monotonic()
        try:
            if timeout_s is not None:
                result = await asyncio.wait_for(_invoke_tool(fn, args, kwargs), timeout=timeout_s)
            else:
                result = await _invoke_tool(fn, args, kwargs)
        except asyncio.TimeoutError as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            event = ToolCallEvent(
                run_id=ctx.run.id, tool=name, attempt=attempt, outcome="timeout",
                duplicate=duplicate, is_fallback=is_fallback, latency_ms=latency_ms, error=str(exc),
            )
            await repository.save_tool_call(event)
            ctx.record_event("TOOL_CALL", event.model_dump(mode="json"))
            ctx.consecutive_tool_failures += 1
            _check_consecutive_failures(name)
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised for the caller to decide
            latency_ms = (time.monotonic() - t0) * 1000
            event = ToolCallEvent(
                run_id=ctx.run.id, tool=name, attempt=attempt, outcome="failure",
                duplicate=duplicate, is_fallback=is_fallback, latency_ms=latency_ms, error=str(exc),
            )
            await repository.save_tool_call(event)
            ctx.record_event("TOOL_CALL", event.model_dump(mode="json"))
            ctx.consecutive_tool_failures += 1
            _check_consecutive_failures(name)
            raise
        latency_ms = (time.monotonic() - t0) * 1000
        ctx.consecutive_tool_failures = 0
        event = ToolCallEvent(
            run_id=ctx.run.id, tool=name, attempt=attempt, outcome="success",
            duplicate=duplicate, is_fallback=is_fallback, latency_ms=latency_ms,
        )
        await repository.save_tool_call(event)
        ctx.record_event("TOOL_CALL", event.model_dump(mode="json"))
        return result

    last_exc: Exception | None = None
    for attempt in range(1, max(1, primary_attempts) + 1):
        try:
            return await _attempt(tool_name, attempt, is_fallback=False)
        except CircuitBreakerTripped:
            # A hard, run-level stop always bypasses the retry/fallback/
            # replan recovery below — never swallowed into last_exc.
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    # Primary exhausted — consult the tool's reliability profile before
    # ever trying a registered alternative.
    alt = registry.get_alternative(tool_name)
    profile_engine = ToolProfileEngine(repository)
    profile = await profile_engine.profile(tool_name)

    should_fall_back = alt is not None and (
        profile.reliability == "INSUFFICIENT_DATA"
        or (profile.success_rate is not None and profile.success_rate < alt.reliability_threshold)
    )

    if should_fall_back and registry.get_callable(alt.fallback) is not None:
        ctx.record_event(
            "TOOL_SUBSTITUTED",
            {
                "primary": tool_name,
                "fallback": alt.fallback,
                "reason": str(last_exc),
                "primary_reliability": profile.reliability,
                "primary_success_rate": profile.success_rate,
                "reliability_threshold": alt.reliability_threshold,
            },
        )
        ctx.actions.append(
            {"action": f"tool_substitution:{tool_name}->{alt.fallback}", "evidence": {"reason": str(last_exc)}}
        )
        try:
            return await _attempt(alt.fallback, 1, is_fallback=True)
        except CircuitBreakerTripped:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    # No reliable/registered alternative, or the fallback also failed.
    if ctx.run.policy.on_tool_exhausted == "human":
        await request_approval(
            f"tool_exhausted:{tool_name}",
            reason=f"tool {tool_name!r} failed and no reliable alternative was available: {last_exc}",
            evidence={"tool": tool_name, "error": str(last_exc)},
        )
        # An APPROVE here means "proceed despite the tool failure" — there
        # is no well-defined "resume the tool call" semantic, so approval
        # simply lets the agent's own subsequent code decide what to do
        # next (perform_action already raised on reject/timeout/replan).
        return None

    raise ReplanRequested(
        f"tool {tool_name!r} failed" + (f"; fallback {alt.fallback!r} unavailable or also failed" if alt else "; no alternative registered"),
        context={"tool": tool_name, "error": str(last_exc)},
    )
