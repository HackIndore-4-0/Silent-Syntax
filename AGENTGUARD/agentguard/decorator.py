"""The @monitor decorator — AgentGuard's primary developer-facing API.

Phase 1 implemented the core loop:

    Agent execution -> Run created -> span captured -> state captured
    -> persisted -> constraint evaluated -> Decision Engine
    -> CONTINUE or STOP -> decision persisted

Phase 2 extends the same wrapper with:

    - a bounded RETRY/REPLAN execution loop (agent code raises
      `agentguard.errors.TransientError` / `ReplanRequested` to signal
      either; decorator.py owns the loop and the bounds — see
      Policy.retry_limit / max_replans)
    - synchronous, local policy guardrails via `agentguard.perform_action`
      (forbidden actions raise immediately; approval-gated actions block
      on a live human decision, still inside the same coroutine — no
      checkpoint/rollback needed, since it is just an `await`)
    - the Reliability Engine (Root Cause + Risk + Confidence) run after
      the deterministic evaluators, feeding a richer Decision Engine
      call that can also reach HUMAN
    - a background (never-awaited-before-return) LLM Judge task — this
      is what keeps it off @monitor's synchronous critical path (Rule 15)

Phase 3 adds, all additive on top of the same loop:

    - one Checkpoint per recorded StateSnapshot, persisted at the end of
      every run (checkpoint/engine.py) — a rollback target
    - a SHA-256 hash-chained AuditEvent trail (audit/chain.py),
      accumulated in-memory as (event_type, payload) pairs via
      RunContext.audit_log and persisted in ONE batch at the very end of
      the run, so per-event hash chaining never costs a database
      round-trip per event
    - a one-shot "recovery seed" (recovery/seed.py): if
      agentguard.recovery.seed_recovery_state() was called before this
      invocation, the run starts from that restored state instead of
      Policy.max_cost's default, and records parent_run_id/
      recovery_checkpoint_id — this is what lets a rolled-back run
      actually continue via a fresh @monitor call (see
      agentguard/recovery/rollback.py and
      examples/budget_failure_recovery.py)

Rollback itself, counterfactual analysis, and audit-chain verification
are NOT part of this loop — they are separate, explicitly-invoked
operations (agentguard/recovery/, agentguard/audit/chain.py) that never
run on a monitored agent's own synchronous response path.

Backward compatibility: `@monitor` bare and `@monitor(policy=Policy(...))`
on sync or async functions behave exactly as in Phase 1 when the agent
never touches any Phase 2 primitive (perform_action/update_state calls
beyond the original single constraint field, TransientError,
ReplanRequested). Nothing here changes Phase 1's CONTINUE/STOP/FAILED
outcomes for a Phase-1-shaped agent.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from .audit.chain import build_chain
from .checkpoint.engine import CheckpointEngine
from .context import RunContext, _reset_current, _set_current, seed_initial_snapshot
from .decision.engine import DecisionEngine
from .errors import CircuitBreakerTripped, ForbiddenActionError, HumanRejected, ReplanRequested, TransientError
from .evaluators.base import Evaluator
from .evaluators.llm_judge import AsyncEvaluator, LLMJudge
from .evaluators.rule_based import ConstraintAdherenceEvaluator
from .models import AgentState, ModelAlternative, Policy, Run, RunStatus
from .otel.instrumentation import start_agent_span, start_decision_span
from .policy.engine import PolicyEngine
from .recovery.seed import pop_recovery_seed
from .reliability.engine import ReliabilityEngine
from ._runtime import get_repository

F = TypeVar("F", bound=Callable[..., Any])

logger = logging.getLogger("agentguard")

_AGENT_SPAN_NAME = "agentguard.invoke_agent"

# Fire-and-forget background tasks (LLM Judge, chiefly). Kept alive here
# so they aren't garbage-collected mid-flight; `wait_for_background_tasks`
# is a test/CLI-only helper for deterministically observing their result
# — @monitor itself never awaits this set (that would put it back on the
# synchronous critical path).
_background_tasks: set[asyncio.Task] = set()


def _track_background(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def wait_for_background_tasks() -> None:
    tasks = list(_background_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _default_evaluators() -> list[Evaluator]:
    return [ConstraintAdherenceEvaluator()]


def _initial_state_for(policy: Policy) -> AgentState:
    # Seeds the constraint into state under the same field name the
    # default ConstraintAdherenceEvaluator checks, so the canonical
    # "max_cost declared -> max_budget observed" demo scenario works
    # with zero agent-side boilerplate. An agent that calls
    # agentguard.update_state(...) simply overwrites this.
    if policy.max_cost is not None:
        return AgentState(max_budget=policy.max_cost)
    return AgentState()


def _task_from_args(args: tuple[Any, ...]) -> str | None:
    return str(args[0]) if args else None


async def _run_llm_judge_background(judge: AsyncEvaluator, run: Run, repository: Any) -> None:
    """Runs off the synchronous critical path. Started with
    asyncio.create_task and never awaited by `_execute` — this function
    persists its own result once the model call completes.
    """
    t0 = time.monotonic()
    try:
        result = await judge.evaluate(run)
    except Exception:  # noqa: BLE001 - a failed background judge must never crash the process
        logger.exception("agentguard: LLM judge failed for run %s", run.id)
        return
    elapsed = time.monotonic() - t0
    result.evidence["async_elapsed_s"] = elapsed
    await repository.save_evaluation(run, result)
    await _apply_async_judge_result(run, result, repository)


async def _apply_async_judge_result(run: Run, judge_result: Any, repository: Any) -> None:
    """The LLM judge never issues a decision itself (Rule: "The Decision
    Engine remains authoritative"). If it flags a run unsafe with
    reasonable confidence *after* that run already went CONTINUE, this
    records a fresh, explicit STOP Decision through a real DecisionEngine
    instance — a new audit row, never a silent mutation of the original
    decision.
    """
    if judge_result.passed or judge_result.confidence < 0.6:
        return
    current = await repository.get_run(run.id)
    if current is None or current.get("status") != RunStatus.CONTINUE.value:
        return

    engine = DecisionEngine()
    engine.state = RunStatus.EVALUATING
    decision = engine.stop(
        reason=f"LLM judge asynchronously flagged this run unsafe after CONTINUE: {judge_result.reason}",
        evidence={"llm_judge": judge_result.model_dump()},
    )
    run.status = RunStatus.STOP
    await repository.set_run_status(run.id, RunStatus.STOP)
    await repository.save_decision(run, decision)


async def _register_pending_human_review(run: Run, decision: Any, reliability: Any, repository: Any) -> None:
    """The post-hoc "low confidence + high impact" HUMAN escalation:
    nothing is blocked in `await` (the agent function already returned),
    so this just persists a pending HumanDecision and notifies any
    connected dashboard — resolved later via
    POST /runs/{run_id}/human-decision (see human/resolution.py).
    """
    from .human.broker import get_broker
    from .models import HumanDecision

    request = HumanDecision(
        run_id=run.id,
        action=None,
        decision=RunStatus.HUMAN,
        risk_score=decision.risk_score,
        confidence=decision.confidence,
        reason=decision.reason,
        evidence=decision.evidence,
        timeout_s=run.policy.human_timeout_s,
    )
    await repository.save_human_decision(request)
    await get_broker().notify(run.id, {"type": "human_review_pending", "request": request.model_dump(mode="json")})


async def _execute(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    policy: Policy,
    evaluators: list[Evaluator],
    is_coroutine: bool,
    llm_judge: AsyncEvaluator | None,
    agent_version: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> Any:
    repository = get_repository()
    policy_engine = PolicyEngine()
    reliability_engine = ReliabilityEngine()

    recovery_seed = pop_recovery_seed()
    if recovery_seed is not None:
        initial_state = AgentState(**recovery_seed["state"])
    else:
        initial_state = _initial_state_for(policy)

    agent_name = getattr(fn, "__name__", "agent")
    agent_id: str | None = None
    if workspace_id is not None:
        from .auth.service import resolve_agent

        agent = await resolve_agent(repository, workspace_id=workspace_id, project_id=project_id, agent_name=agent_name)
        agent_id = agent.id

    run = Run(
        agent_name=agent_name,
        task=_task_from_args(args),
        policy=policy,
        initial_state=initial_state.snapshot(),
        parent_run_id=recovery_seed.get("parent_run_id") if recovery_seed else None,
        recovery_checkpoint_id=recovery_seed.get("checkpoint_id") if recovery_seed else None,
        agent_version=agent_version,
        workspace_id=workspace_id,
        project_id=project_id,
        agent_id=agent_id,
    )

    ctx = RunContext(run=run, state=initial_state)
    seed_initial_snapshot(ctx)
    token = _set_current(ctx)

    async def _save_decision(decision: Any) -> None:
        await repository.save_decision(run, decision)
        ctx.record_event(
            "DECISION",
            {
                "outcome": decision.outcome.value,
                "reason": decision.reason,
                "risk_score": decision.risk_score,
                "confidence": decision.confidence,
                "retry_count": decision.retry_count,
                "replan_count": decision.replan_count,
            },
        )

    await repository.create_run(run)
    if run.policy.llm_gateway is not None and run.policy.llm_gateway.fallback_chain:
        # Reflects real configured LLM-gateway fallback routing on the
        # Models dashboard page's "Registered Fallbacks" panel —
        # previously entirely disconnected from this table (see
        # AGENTGUARD.md's Tier 3 notes). save_model_alternative() is
        # already an upsert keyed on (workspace_id, primary_model), so
        # this is safe to repeat on every run without accumulating rows.
        for alt in run.policy.llm_gateway.fallback_chain:
            await repository.save_model_alternative(
                ModelAlternative(
                    primary_model=alt.primary_model, fallback_model=alt.fallback_model,
                    reliability_threshold=alt.reliability_threshold, workspace_id=run.workspace_id,
                )
            )
    ctx.record_event(
        "RUN_START",
        {
            "run_id": run.id,
            "agent_name": run.agent_name,
            "task": run.task,
            "policy_version": policy.version,
            "initial_state": run.initial_state,
            "parent_run_id": run.parent_run_id,
            "recovery_checkpoint_id": run.recovery_checkpoint_id,
        },
    )

    error: BaseException | None = None
    terminal_exc: BaseException | None = None
    result: Any = None
    decision_engine = DecisionEngine()
    attempt = 0

    try:
        while True:
            attempt += 1
            attempt_error: BaseException | None = None

            with start_agent_span(run, attempt=attempt) as span:
                span_ctx = span.get_span_context()
                run.trace_id = format(span_ctx.trace_id, "032x")
                run.span_id = format(span_ctx.span_id, "016x")
                try:
                    if is_coroutine:
                        result = await fn(*args, **kwargs)
                    else:
                        result = fn(*args, **kwargs)
                except (
                    TransientError, ReplanRequested, HumanRejected, ForbiddenActionError, CircuitBreakerTripped,
                ) as exc:
                    attempt_error = exc
                    span.record_exception(exc)
                # Any other exception is NOT caught here: it propagates
                # out of this `with` block, triggers start_agent_span's
                # own except-clause (records it, sets span status ERROR),
                # and continues propagating to the `except BaseException`
                # below — the Phase 1 FAILED path, unchanged.

            await repository.save_span(
                run,
                name=_AGENT_SPAN_NAME,
                trace_id=run.trace_id,
                span_id=run.span_id,
                start_time=run.started_at,
                end_time=datetime.now(timezone.utc),
                attributes={
                    "gen_ai.agent.name": run.agent_name,
                    "agentguard.policy.max_cost": policy.max_cost,
                    "agentguard.attempt": attempt,
                },
            )
            ctx.record_event(
                "AGENT_STEP",
                {
                    "attempt": attempt,
                    "trace_id": run.trace_id,
                    "span_id": run.span_id,
                    "error_type": type(attempt_error).__name__ if attempt_error else None,
                    "error": str(attempt_error) if attempt_error else None,
                    "loop_iterations": ctx.loop_iterations,
                    "consecutive_tool_failures": ctx.consecutive_tool_failures,
                },
            )

            if attempt_error is None:
                break  # success: fall through to evaluation, in `finally`

            if isinstance(attempt_error, HumanRejected):
                decision = decision_engine.stop(
                    reason=f"human rejected action {attempt_error.action!r}: {attempt_error.reason}",
                    evidence={"action": attempt_error.action, "reason": attempt_error.reason},
                )
                await _save_decision(decision)
                run.status = RunStatus.STOP
                terminal_exc = attempt_error
                break

            if isinstance(attempt_error, ForbiddenActionError):
                decision = decision_engine.stop(
                    reason=str(attempt_error), evidence={"action": attempt_error.action}
                )
                await _save_decision(decision)
                run.status = RunStatus.STOP
                terminal_exc = attempt_error
                break

            if isinstance(attempt_error, CircuitBreakerTripped):
                # A hard, run-level guardrail — never retried/replanned
                # (the whole point of a circuit breaker is "don't try
                # again"), same immediate-STOP precedent as
                # ForbiddenActionError. The exact triggering node
                # (code_file/function/lineno, captured in the agent's OWN
                # code by call_tool()) is recorded as decision evidence so
                # the halt is traceable to a real line, not just a rule
                # name.
                decision = decision_engine.stop(
                    reason=str(attempt_error),
                    evidence={
                        "rule": attempt_error.rule,
                        "detail": attempt_error.detail,
                        "tool": attempt_error.tool,
                        "code_file": attempt_error.code_file,
                        "code_function": attempt_error.code_function,
                        "code_lineno": attempt_error.code_lineno,
                        "loop_iterations": ctx.loop_iterations,
                        "consecutive_tool_failures": ctx.consecutive_tool_failures,
                    },
                )
                await _save_decision(decision)
                run.status = RunStatus.STOP
                terminal_exc = attempt_error
                break

            if isinstance(attempt_error, ReplanRequested):
                if ctx.replan_count < policy.max_replans:
                    ctx.replan_count += 1
                    run.replan_count = ctx.replan_count
                    # A replan is a new plan: it gets its own fresh retry
                    # budget rather than inheriting exhaustion from the
                    # plan that just got replaced (otherwise a replanned
                    # attempt could immediately re-exhaust a budget it
                    # never actually spent).
                    ctx.retry_count = 0
                    run.retry_count = 0
                    ctx.replan_context = {"reason": attempt_error.reason, **attempt_error.context}
                    decision = decision_engine.replan(
                        reason=attempt_error.reason,
                        replan_count=ctx.replan_count,
                        evidence={"context": attempt_error.context},
                    )
                    await _save_decision(decision)
                    decision_engine = DecisionEngine()
                    continue

                decision = decision_engine.replan(
                    reason=f"{attempt_error.reason} (replan budget exhausted)",
                    replan_count=ctx.replan_count,
                    evidence={"context": attempt_error.context},
                )
                await _save_decision(decision)
                decision = decision_engine.stop(
                    reason=f"replan limit ({policy.max_replans}) exceeded", evidence={}
                )
                await _save_decision(decision)
                run.status = RunStatus.STOP
                terminal_exc = attempt_error
                break

            if isinstance(attempt_error, TransientError):
                if ctx.retry_count < policy.retry_limit:
                    ctx.retry_count += 1
                    run.retry_count = ctx.retry_count
                    decision = decision_engine.retry(
                        reason=str(attempt_error),
                        retry_count=ctx.retry_count,
                        evidence={"exception": str(attempt_error), **attempt_error.evidence},
                    )
                    await _save_decision(decision)
                    decision_engine = DecisionEngine()
                    continue

                decision = decision_engine.retry(
                    reason=f"{attempt_error} (retry budget exhausted)",
                    retry_count=ctx.retry_count,
                    evidence={"exception": str(attempt_error)},
                )
                await _save_decision(decision)

                if policy.on_retry_exhausted == "replan" and ctx.replan_count < policy.max_replans:
                    ctx.replan_count += 1
                    run.replan_count = ctx.replan_count
                    ctx.retry_count = 0
                    run.retry_count = 0
                    ctx.replan_context = {
                        "reason": "retry_limit_exceeded",
                        "last_error": str(attempt_error),
                    }
                    decision = decision_engine.replan(
                        reason=f"retry limit ({policy.retry_limit}) exceeded, replanning",
                        replan_count=ctx.replan_count,
                        evidence={},
                    )
                    await _save_decision(decision)
                    decision_engine = DecisionEngine()
                    continue

                decision = decision_engine.stop(
                    reason=f"retry limit ({policy.retry_limit}) exceeded", evidence={}
                )
                await _save_decision(decision)
                run.status = RunStatus.STOP
                terminal_exc = attempt_error
                break

    except BaseException as exc:  # noqa: BLE001 - re-raised below
        error = exc
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        run.final_state = ctx.state.snapshot()
        run.actions = ctx.actions

        # Phase 3: one Checkpoint per recorded StateSnapshot (S1, S2,
        # S3, ...), persisted regardless of how the run ended — a
        # rollback target must exist even for a run that STOPped or
        # FAILED. Never serializes anything beyond the same JSON-safe
        # dict StateSnapshot.data already is.
        checkpoint_engine = CheckpointEngine()
        checkpoints = [checkpoint_engine.create(run.id, snap) for snap in ctx.state_history]
        for checkpoint in checkpoints:
            await repository.save_checkpoint(checkpoint)
            ctx.record_event(
                "CHECKPOINT",
                {"checkpoint_id": checkpoint.id, "label": checkpoint.label, "state_hash": checkpoint.state_hash},
            )

        if error is not None:
            run.status = RunStatus.FAILED
            run.exception_type = type(error).__name__
            run.exception_message = str(error)
            await repository.update_run(run)
        elif terminal_exc is not None:
            await repository.update_run(run)
        else:
            eval_results = [evaluator.evaluate(run) for evaluator in evaluators]
            for eval_result in eval_results:
                await repository.save_evaluation(run, eval_result)
                ctx.record_event(
                    "EVALUATION",
                    {
                        "evaluator": eval_result.evaluator,
                        "passed": eval_result.passed,
                        "score": eval_result.score,
                        "label": eval_result.label,
                        "confidence": eval_result.confidence,
                    },
                )

            policy_findings = policy_engine.evaluate(run)
            ctx.record_event(
                "POLICY_CHECK",
                {"phase": "post_execution", "findings": [f.model_dump() for f in policy_findings]},
            )
            reliability = reliability_engine.assess(
                run.id,
                policy=policy,
                eval_results=eval_results,
                policy_findings=policy_findings,
                state_history=ctx.state_history,
            )
            await repository.save_risk_assessment(run.id, reliability.risk)
            ctx.record_event("RISK_ASSESSMENT", reliability.risk.model_dump())
            if reliability.root_cause is not None:
                await repository.save_root_cause(run.id, reliability.root_cause)
                ctx.record_event("ROOT_CAUSE", reliability.root_cause.model_dump())

            decision_engine.begin_evaluation()
            decision = decision_engine.decide(
                eval_results, policy_findings=policy_findings, reliability=reliability, policy=policy
            )
            decision.retry_count = ctx.retry_count
            decision.replan_count = ctx.replan_count
            run.status = decision.outcome
            await repository.update_run(run)
            await _save_decision(decision)
            with start_decision_span(run, decision):
                pass

            if decision.outcome == RunStatus.HUMAN:
                await _register_pending_human_review(run, decision, reliability, repository)

            if llm_judge is not None:
                task = asyncio.create_task(_run_llm_judge_background(llm_judge, run, repository))
                _track_background(task)

        ctx.record_event(
            "RUN_COMPLETION",
            {
                "status": run.status.value,
                "final_state": run.final_state,
                "duration_ms": run.duration_ms,
                "retry_count": run.retry_count,
                "replan_count": run.replan_count,
                "loop_iterations": ctx.loop_iterations,
                "consecutive_tool_failures": ctx.consecutive_tool_failures,
            },
        )
        for event in build_chain(run.id, ctx.audit_log, policy_version=policy.version):
            await repository.save_audit_event(event)

        from .tracing._pending import drain_pending_trace_writes

        await drain_pending_trace_writes()

        _reset_current(token)

        if terminal_exc is not None:
            raise terminal_exc

    return result


def monitor(
    func: F | None = None,
    *,
    policy: Policy | None = None,
    evaluators: list[Evaluator] | None = None,
    llm_judge: AsyncEvaluator | bool | None = True,
    agent_version: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> Any:
    """Wrap an agent function with AgentGuard's reliability loop.

    Usage (unchanged from Phase 1):
        @monitor
        def my_agent(task): ...

        @monitor(policy=Policy(max_cost=60000))
        async def my_agent(task): ...

    `agent_version` (Phase 4): an optional caller-supplied label recorded
    on every Run this decoration produces (`Run.agent_version`) — what
    Regression Comparison groups/labels runs by ("Agent v1" vs
    "Agent v2"). Purely informational; nothing else in the SDK branches
    on it.

    `workspace_id`/`project_id` (Dashboard V2): the multi-tenant
    ownership boundary this run belongs to. Plain `@monitor` never sets
    these (None — unchanged, single-tenant behavior); `agentguard.client.AgentGuard.monitor`
    (an authenticated SDK client resolved from an API key) sets both
    automatically. When set, the agent's name is also auto-registered as
    an `AgentRegistration` within that workspace on first use.

    Phase 2 additions, all opt-in via the agent's own code:
        - raise agentguard.errors.TransientError(...) for a bounded RETRY
        - raise agentguard.errors.ReplanRequested(reason=...) for REPLAN
        - `await agentguard.perform_action("payment", ...)` for a
          synchronous forbidden-action guardrail / human-approval gate

    `llm_judge`: True (default) runs the LLM-as-Judge evaluator as a
    background task after every run (see evaluators/llm_judge.py and
    llm/provider.py for what "LLM" means in the current environment).
    Pass False to disable it, or a custom AsyncEvaluator instance to use
    a specific provider.

    Both sync and async functions are supported. The decorator never
    changes the wrapped function's return value and never swallows an
    exception the function raises (Rule 9) — it only records it. This
    now includes Phase 2's own control-flow exceptions once their
    retry/replan budget is exhausted, or a human rejects/forbids an
    action: they still propagate to the caller, with run.status=STOP
    (a controlled guardrail rejection) rather than FAILED (an
    unrecognized bug), and — unlike FAILED — a real Decision is recorded.

    Note: a sync agent function is run via asyncio.run() internally, so
    it cannot itself be called from inside an already-running event
    loop, and cannot use `perform_action`/`request_approval` (those are
    async). Define the agent as `async def` if either matters.
    """

    def decorate(target: F) -> F:
        resolved_policy = policy if policy is not None else Policy()
        PolicyEngine().validate(resolved_policy)
        resolved_evaluators = evaluators if evaluators is not None else _default_evaluators()

        resolved_judge: AsyncEvaluator | None
        if llm_judge is True:
            resolved_judge = LLMJudge()
        elif llm_judge is False or llm_judge is None:
            resolved_judge = None
        else:
            resolved_judge = llm_judge

        is_coroutine = inspect.iscoroutinefunction(target)

        if is_coroutine:

            @functools.wraps(target)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await _execute(
                    target, args, kwargs, resolved_policy, resolved_evaluators, True, resolved_judge,
                    agent_version, workspace_id, project_id,
                )

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(target)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return asyncio.run(
                _execute(
                    target, args, kwargs, resolved_policy, resolved_evaluators, False, resolved_judge,
                    agent_version, workspace_id, project_id,
                )
            )

        return sync_wrapper  # type: ignore[return-value]

    if func is not None:
        return decorate(func)
    return decorate
