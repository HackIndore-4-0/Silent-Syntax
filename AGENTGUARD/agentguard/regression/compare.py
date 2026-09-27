"""Regression Comparison — Phase 4.

`compare_runs(repository, run_a_id, run_b_id)` compares two individual
runs. `compare_metrics(metrics_a, metrics_b)` is the generic primitive
underneath it, also used for BATCH comparison (agentguard/regression/corpus.py,
the CI gate, and DSPy candidate validation) by feeding it aggregate
(mean) metric dicts computed across many runs instead of one.

Every comparison reuses the exact same six-dimension Reliability Report
builder Phase 3 already ships (agentguard/reliability/report.py) — no
duplicated scoring logic. Rule: never label a winner, never silently
treat an unavailable metric as 0 — an unavailable metric on either side
makes that row `available=False` with `delta=None`, always shown, never
hidden or defaulted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..reliability.report import ReliabilityReport, build_reliability_report

METRIC_ORDER = [
    "correctness",
    "goal_completion",
    "constraint_adherence",
    "decision_consistency",
    "tool_usage",
    "behavioral_reliability",
    "risk",
    "confidence",
    "retry_count",
    "replan_count",
    "human_intervention_count",
    "failure_count",
    "latency_ms",
]


@dataclass
class MetricDelta:
    metric: str
    value_a: float | None
    value_b: float | None
    delta: float | None
    available: bool

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "value_a": self.value_a, "value_b": self.value_b, "delta": self.delta, "available": self.available}


@dataclass
class ComparisonResult:
    label_a: str
    label_b: str
    metrics: list[MetricDelta] = field(default_factory=list)

    def get(self, metric: str) -> MetricDelta | None:
        return next((m for m in self.metrics if m.metric == metric), None)

    def to_dict(self) -> dict[str, Any]:
        return {"label_a": self.label_a, "label_b": self.label_b, "metrics": [m.to_dict() for m in self.metrics]}


def compare_metrics(
    metrics_a: dict[str, float | None], metrics_b: dict[str, float | None], *, label_a: str = "A", label_b: str = "B"
) -> ComparisonResult:
    keys = [k for k in METRIC_ORDER if k in metrics_a or k in metrics_b]
    deltas = []
    for k in keys:
        va, vb = metrics_a.get(k), metrics_b.get(k)
        available = va is not None and vb is not None
        delta = (vb - va) if available else None
        deltas.append(MetricDelta(metric=k, value_a=va, value_b=vb, delta=delta, available=available))
    return ComparisonResult(label_a=label_a, label_b=label_b, metrics=deltas)


def report_metrics(run: dict[str, Any], report: ReliabilityReport) -> dict[str, float | None]:
    dims = report.to_dict()["dimensions"]
    decisions = run.get("decisions") or []
    return {
        "correctness": dims["correctness"]["value"],
        "goal_completion": dims["goal_completion"]["value"],
        "constraint_adherence": dims["constraint_adherence"]["value"],
        "decision_consistency": dims["decision_consistency"]["value"],
        "tool_usage": dims["tool_usage"]["value"],
        "behavioral_reliability": dims["behavioral_reliability"]["value"],
        "risk": report.risk,
        "confidence": report.confidence,
        "retry_count": float(run.get("retry_count", 0)),
        "replan_count": float(run.get("replan_count", 0)),
        "human_intervention_count": float(sum(1 for d in decisions if d.get("outcome") == "human")),
        "failure_count": float(1 if run.get("status") in ("stop", "failed") else 0),
        "latency_ms": run.get("duration_ms"),
    }


async def build_report_for_run(repository: Any, run_id: str) -> tuple[dict[str, Any], ReliabilityReport]:
    run = await repository.get_run(run_id)
    if run is None:
        raise ValueError(f"run {run_id!r} not found")
    risk_assessments = await repository.list_risk_assessments(run_id)
    root_cause = await repository.get_root_cause(run_id)
    audit_events = await repository.list_audit_events(run_id)
    report = build_reliability_report(run, risk_assessments=risk_assessments, root_cause=root_cause, audit_events=audit_events)
    return run, report


async def compare_runs(
    repository: Any, run_a_id: str, run_b_id: str, *, label_a: str | None = None, label_b: str | None = None
) -> ComparisonResult:
    run_a, report_a = await build_report_for_run(repository, run_a_id)
    run_b, report_b = await build_report_for_run(repository, run_b_id)
    metrics_a = report_metrics(run_a, report_a)
    metrics_b = report_metrics(run_b, report_b)
    return compare_metrics(metrics_a, metrics_b, label_a=label_a or run_a_id, label_b=label_b or run_b_id)


def mean_metrics(metric_dicts: list[dict[str, float | None]]) -> dict[str, float | None]:
    """Aggregate many single-run metric dicts into one mean-per-metric
    dict — the primitive BATCH comparison (corpus evaluation, CI gate,
    DSPy candidate validation) is built from. A metric missing/None on
    some runs is averaged only over the runs where it IS available;
    a metric unavailable on EVERY run in the batch is reported as None
    (unavailable), never as 0."""
    if not metric_dicts:
        return {k: None for k in METRIC_ORDER}
    result: dict[str, float | None] = {}
    for key in METRIC_ORDER:
        values = [d[key] for d in metric_dicts if d.get(key) is not None]
        result[key] = (sum(values) / len(values)) if values else None
    return result
