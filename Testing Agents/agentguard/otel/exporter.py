"""Tracing provider setup — the "how spans leave the process" concern.

Kept separate from instrumentation.py (the "what attributes we attach"
concern, Rule 3) so the exporter can be swapped (console, OTLP, an
in-memory test double, ...) without touching span-creation code anywhere
else in the SDK.

Phase 3 adds OTLP export — the interoperability demonstration the spec
asks for (AgentGuard -> an OpenTelemetry Collector -> Jaeger or any
other OTLP-compatible viewer), on top of Phase 1/2's console exporter,
which remains the default. Nothing here is proprietary: every span this
SDK produces is a standard `opentelemetry.sdk.trace.Span`, exported
through the standard OTLP/HTTP protocol when enabled.
"""
from __future__ import annotations

import os
import threading

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter

_lock = threading.Lock()
_configured = False


def _build_otlp_exporter() -> SpanExporter:
    """Deferred import: the `opentelemetry-exporter-otlp-proto-http`
    package is an optional dependency (see pyproject.toml's `otel-otlp`
    extra) — importing this module must never require it unless OTLP
    export is actually requested via AGENTGUARD_OTEL_EXPORTER=otlp."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    endpoint = os.environ.get("AGENTGUARD_OTEL_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    return OTLPSpanExporter(endpoint=endpoint)


def _resolve_exporter() -> SpanExporter | None:
    """AGENTGUARD_OTEL_EXPORTER selects the exporter:

        console (default)  ConsoleSpanExporter — human-readable, stdout,
                            no external process required.
        otlp                OTLPSpanExporter/HTTP -> AGENTGUARD_OTEL_OTLP_ENDPOINT
                            (default http://localhost:4318/v1/traces) —
                            a real OpenTelemetry Collector or any
                            OTLP-compatible endpoint (e.g. Jaeger's
                            built-in OTLP receiver).
        none                no exporter configured at all.

    AGENTGUARD_OTEL_CONSOLE=0 is Phase 1/2's original silencing switch
    for the console exporter specifically; it is preserved for backward
    compatibility and only applies when AGENTGUARD_OTEL_EXPORTER is left
    at its "console" default.
    """
    choice = os.environ.get("AGENTGUARD_OTEL_EXPORTER", "console").lower()
    if choice == "otlp":
        return _build_otlp_exporter()
    if choice == "none":
        return None
    if os.environ.get("AGENTGUARD_OTEL_CONSOLE", "1") == "0":
        return None
    return ConsoleSpanExporter()


def configure_tracing(service_name: str = "agentguard") -> TracerProvider:
    """Idempotently configure a global TracerProvider.

    Uses SimpleSpanProcessor, not BatchSpanProcessor: at AgentGuard's
    volume (a handful of spans per run) there is nothing worth batching,
    and SimpleSpanProcessor exports synchronously in the same thread the
    span ends on, with no background export thread that can outlive —
    and then race against — process or interpreter shutdown. This does
    mean OTLP export latency is on the same call as span completion; see
    docs/EXECUTION_REPORT_PHASE_3.md's Latency section for measured
    overhead with OTLP enabled vs. the console/no-op exporters.
    """
    global _configured
    with _lock:
        if _configured:
            return trace.get_tracer_provider()  # type: ignore[return-value]

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        exporter = _resolve_exporter()
        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _configured = True
        return provider


def get_tracer():
    configure_tracing()
    return trace.get_tracer("agentguard")


def reset_tracing_for_tests() -> None:
    """Test-only: allow configure_tracing() to run again with a fresh
    exporter choice. OpenTelemetry's global TracerProvider is otherwise
    a one-shot, process-wide singleton."""
    global _configured
    with _lock:
        _configured = False
