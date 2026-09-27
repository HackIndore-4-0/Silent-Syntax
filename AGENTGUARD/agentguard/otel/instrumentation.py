"""Domain-specific span creation.

Phase 1 creates exactly one span per run: the agent-level invocation.
Tool spans and LLM spans remain Phase 4+ territory. Phase 3 adds one
more, `start_decision_span`, purely additive — `start_agent_span`'s
signature is unchanged (Rule 3) — so a run's Decision Engine outcome is
itself visible as OpenTelemetry span attributes, not just as a database
row, which is what lets an external OTel viewer (Jaeger, etc.) show
*why* AgentGuard stopped a run without querying AgentGuard's own API.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry.trace import Span, Status, StatusCode

from ..models import Run
from .exporter import get_tracer


@contextmanager
def start_agent_span(run: Run, attempt: int = 1) -> Iterator[Span]:
    tracer = get_tracer()
    with tracer.start_as_current_span("agentguard.invoke_agent") as span:
        span.set_attribute("gen_ai.agent.name", run.agent_name)
        span.set_attribute("agentguard.run.id", run.id)
        span.set_attribute("agentguard.attempt", attempt)
        if run.policy.max_cost is not None:
            span.set_attribute("agentguard.policy.max_cost", run.policy.max_cost)
        if run.task is not None:
            span.set_attribute("agentguard.run.task", run.task)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))


@contextmanager
def start_decision_span(run: Run, decision: Any) -> Iterator[Span]:
    """A short-lived span recording the Decision Engine's outcome for
    `run` — the "decision-related attributes" an OTel-based
    interoperability check looks for (see
    docs/EXECUTION_REPORT_PHASE_3.md §13)."""
    tracer = get_tracer()
    with tracer.start_as_current_span("agentguard.decision") as span:
        span.set_attribute("agentguard.run.id", run.id)
        span.set_attribute("agentguard.run.status", run.status.value)
        span.set_attribute("agentguard.decision.outcome", decision.outcome.value)
        span.set_attribute("agentguard.decision.reason", decision.reason)
        if decision.risk_score is not None:
            span.set_attribute("agentguard.decision.risk_score", decision.risk_score)
        if decision.confidence is not None:
            span.set_attribute("agentguard.decision.confidence", decision.confidence)
        span.set_attribute("agentguard.policy.version", run.policy.version)
        yield span


@contextmanager
def start_trace_step_span(name: str, kind: str) -> Iterator[Span]:
    """A child span for one recorded TraceStep (agentguard/tracing/).

    Nesting is automatic, standard OTel context propagation — this must
    be opened from inside the traced function's own call, while an
    ancestor span's `with` block (start_agent_span's, or an outer
    trace-step span's) is still active, exactly like start_agent_span
    nests across retries. Unlike start_decision_span (opened *after*
    start_agent_span's `with` block already closed, so it ends up a
    root span) — don't repeat that mistake here; @traceable/
    wrap_llm_client only ever run inside an active @monitor run, whose
    agent span is guaranteed to still be open by construction."""
    tracer = get_tracer()
    with tracer.start_as_current_span(f"agentguard.trace_step.{kind}") as span:
        span.set_attribute("agentguard.trace_step.name", name)
        span.set_attribute("agentguard.trace_step.kind", kind)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))
