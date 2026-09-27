"""OpenTelemetry interoperability verification (Phase 3 §9/§13).

Verifies, against the real OpenTelemetry SDK (not a reimplementation):
span emitted, run ID associated, decision-related attributes present,
timestamps present, and that the export pathway (SimpleSpanProcessor ->
SpanExporter) actually runs. Uses the SDK's own InMemorySpanExporter so
the assertions are on real exported `ReadableSpan` objects — no live
collector required (this environment has no `docker` — see
docs/EXECUTION_REPORT_PHASE_3.md §1/§13 for the separate OTLP
export-pathway verification against that documented limitation).

Also exercises the OTLP exporter *construction* path (Phase 3's
interoperability addition on top of Phase 1/2's console exporter) to
confirm AgentGuard emits standard OTLP-compatible spans rather than a
proprietary trace format — without requiring a reachable collector.
"""
from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.human.broker import reset_broker
from agentguard.otel import exporter as otel_exporter

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


@pytest.fixture
def memory_span_exporter():
    """The OpenTelemetry SDK deliberately forbids overriding the global
    TracerProvider more than once per process (`trace.set_tracer_provider`
    silently no-ops after the first real call — by design, so production
    code can't have its tracing configuration hijacked mid-process). So
    rather than fighting that, this fixture adds ONE MORE span processor
    (this test's own InMemorySpanExporter) onto whatever provider is
    already configured — exactly how a second, real OTLP exporter would
    be added alongside the console one in production. Every span
    AgentGuard emits from this point in the process forward is captured
    here, which is all a single test needs."""
    provider = otel_exporter.configure_tracing()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


async def test_span_emitted_with_run_id_and_timestamps(memory_span_exporter):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        return "ok"

    await agent("do something")

    spans = memory_span_exporter.get_finished_spans()
    agent_spans = [s for s in spans if s.name == "agentguard.invoke_agent"]
    assert len(agent_spans) == 1
    span = agent_spans[0]

    run_id_attr = span.attributes.get("agentguard.run.id")
    assert run_id_attr is not None and len(run_id_attr) > 0
    assert span.start_time is not None and span.end_time is not None
    assert span.end_time > span.start_time
    assert span.attributes.get("gen_ai.agent.name") == "agent"


async def test_decision_related_attributes_present(memory_span_exporter):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "over budget"

    await drifting_agent("find a laptop")

    spans = memory_span_exporter.get_finished_spans()
    decision_spans = [s for s in spans if s.name == "agentguard.decision"]
    assert len(decision_spans) == 1
    span = decision_spans[0]

    assert span.attributes.get("agentguard.decision.outcome") == "stop"
    assert "unsafe" in span.attributes.get("agentguard.decision.reason", "")
    assert span.attributes.get("agentguard.decision.risk_score") is not None
    assert span.attributes.get("agentguard.decision.confidence") is not None
    assert span.attributes.get("agentguard.run.status") == "stop"
    assert span.attributes.get("agentguard.policy.version") == 1


async def test_export_pathway_actually_runs(memory_span_exporter):
    """The exporter is wired through a real SimpleSpanProcessor
    (agentguard/otel/exporter.py) — if export weren't actually running,
    the in-memory exporter would stay empty even after a run completes."""
    assert memory_span_exporter.get_finished_spans() == ()

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        return "ok"

    await agent("go")

    assert len(memory_span_exporter.get_finished_spans()) >= 1


def test_otlp_exporter_constructs_a_real_standard_exporter(monkeypatch):
    """Confirms the OTLP export path (Phase 3's interoperability
    addition) builds a genuine `opentelemetry-exporter-otlp-proto-http`
    exporter targeting the standard OTLP/HTTP traces endpoint — i.e.
    AgentGuard spans remain OpenTelemetry-compatible, not a proprietary
    format. Construction never requires a reachable collector; only
    `.export()` does (see docs/EXECUTION_REPORT_PHASE_3.md §13 for that
    documented environment limitation)."""
    monkeypatch.setenv("AGENTGUARD_OTEL_EXPORTER", "otlp")
    monkeypatch.setenv("AGENTGUARD_OTEL_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

    exporter = otel_exporter._resolve_exporter()

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    assert isinstance(exporter, OTLPSpanExporter)
