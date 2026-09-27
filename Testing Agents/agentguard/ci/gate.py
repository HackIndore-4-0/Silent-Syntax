"""CI/CD Reliability Gate — Phase 4.

Deterministic regression policy (documented, not invented per call):

    A deployment is BLOCKED if any PROTECTED metric's candidate value is
    more than `threshold_pct` percentage points BELOW its baseline
    value. Metrics that improve, or regress by less than the threshold,
    ALLOW. A metric unavailable on either side (no data) never
    participates in the gate at all — it is skipped, never treated as a
    failure and never treated as a pass.

By default the six Phase 3 Reliability Report dimensions are the
protected set (the "safety" metrics the spec's example — constraint
adherence — belongs to); callers may narrow or widen that list
explicitly via `protected_metrics`, but the gate never invents its own
weighting formula across them: each protected metric is checked
independently against the same threshold.
"""
from __future__ import annotations

from ..models import CIGateResult
from ..regression.compare import METRIC_ORDER
from ..regression.corpus import evaluate_run_batches

DEFAULT_PROTECTED_METRICS = [
    "correctness",
    "goal_completion",
    "constraint_adherence",
    "decision_consistency",
    "tool_usage",
    "behavioral_reliability",
]


class CIGateError(ValueError):
    pass


async def run_ci_gate(
    repository,
    baseline_run_ids: list[str],
    candidate_run_ids: list[str],
    *,
    threshold_pct: float,
    protected_metrics: list[str] | None = None,
) -> CIGateResult:
    if not baseline_run_ids or not candidate_run_ids:
        raise CIGateError("ci-gate requires at least one baseline run and one candidate run")
    if threshold_pct < 0:
        raise CIGateError("threshold_pct must be >= 0")

    protected = protected_metrics or DEFAULT_PROTECTED_METRICS
    unknown = [m for m in protected if m not in METRIC_ORDER]
    if unknown:
        raise CIGateError(f"unknown protected metric(s): {unknown}")

    comparison = await evaluate_run_batches(repository, baseline_run_ids, candidate_run_ids)

    threshold_fraction = threshold_pct / 100.0
    regressions: dict[str, float] = {}
    for metric in protected:
        delta = comparison.get(metric)
        if delta is None or not delta.available or delta.delta is None:
            continue  # Rule: never block because a metric is unavailable.
        if delta.delta < -threshold_fraction:
            regressions[metric] = delta.delta * 100  # percentage points, negative = regression

    result: CIGateResult = CIGateResult(
        baseline_run_ids=baseline_run_ids,
        candidate_run_ids=candidate_run_ids,
        threshold_pct=threshold_pct,
        protected_metrics=protected,
        regressions=regressions,
        result="block" if regressions else "pass",
    )
    await repository.save_ci_gate_result(result)
    return result
