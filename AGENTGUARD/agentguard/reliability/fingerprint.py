"""Agent Behavior Fingerprinting — Phase 4.

A per-agent behavioral baseline, computed fresh from persisted run
history each time (same "recompute, don't cache" principle as
tool_profile.py and Phase 3's Reliability Report). Requires enough
history to be statistically meaningful (Rule: "Do not report a
statistically meaningful baseline from one run") — below
MIN_SAMPLE_SIZE prior runs, `baseline`/`current`/`deviation` are all
None and `anomaly` is always False, never a guess.
"""
from __future__ import annotations

from typing import Any

from ..models import BehaviorFingerprint

MIN_SAMPLE_SIZE = 5
ANOMALY_THRESHOLD = 0.5
"""A tracked metric is flagged anomalous when its relative deviation
from baseline exceeds 50% — a documented, fixed threshold, not tuned
per agent."""

_TRACKED_METRICS = ("tool_calls", "retry_count", "latency_ms")
"""The per-run-comparable metrics deviation/anomaly are computed over.
completion_rate/failure_rate/human_intervention_frequency are baseline-
only descriptive statistics (a single run has no directly comparable
"rate" of its own)."""


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _relative_deviation(baseline: float | None, current: float | None) -> float | None:
    if baseline is None or current is None:
        return None
    if baseline == 0:
        return None if current == 0 else 1.0
    return (current - baseline) / baseline


def build_behavior_fingerprint(
    agent_name: str,
    *,
    historical_runs: list[dict[str, Any]],
    historical_tool_call_counts: list[int],
    current_run: dict[str, Any] | None = None,
    current_tool_call_count: int | None = None,
) -> BehaviorFingerprint:
    n = len(historical_runs)
    if n < MIN_SAMPLE_SIZE:
        return BehaviorFingerprint(agent_name=agent_name, sample_count=n, min_sample_size=MIN_SAMPLE_SIZE)

    retry_counts = [r.get("retry_count", 0) for r in historical_runs]
    durations = [r["duration_ms"] for r in historical_runs if r.get("duration_ms") is not None]
    completions = sum(1 for r in historical_runs if r.get("status") == "continue")
    failures = sum(1 for r in historical_runs if r.get("status") in ("stop", "failed"))
    human_interventions = sum(
        1 for r in historical_runs if any(d.get("outcome") == "human" for d in (r.get("decisions") or []))
    )

    baseline = {
        "tool_calls": _mean([float(c) for c in historical_tool_call_counts]) or 0.0,
        "retry_count": _mean([float(c) for c in retry_counts]) or 0.0,
        "latency_ms": _mean(durations),
        "completion_rate": completions / n,
        "failure_rate": failures / n,
        "human_intervention_frequency": human_interventions / n,
    }

    current: dict[str, float] | None = None
    deviation: dict[str, float | None] | None = None
    anomaly = False

    if current_run is not None:
        current = {
            "tool_calls": float(current_tool_call_count or 0),
            "retry_count": float(current_run.get("retry_count", 0)),
            "latency_ms": current_run.get("duration_ms"),
        }
        deviation = {m: _relative_deviation(baseline.get(m), current.get(m)) for m in _TRACKED_METRICS}
        anomaly = any(d is not None and abs(d) > ANOMALY_THRESHOLD for d in deviation.values())

    return BehaviorFingerprint(
        agent_name=agent_name,
        sample_count=n,
        baseline=baseline,
        current=current,
        deviation=deviation,
        anomaly=anomaly,
        min_sample_size=MIN_SAMPLE_SIZE,
        anomaly_threshold=ANOMALY_THRESHOLD,
    )


class FingerprintEngine:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def fingerprint(self, agent_name: str, *, current_run_id: str | None = None) -> BehaviorFingerprint:
        all_runs = await self._repository.list_runs_by_agent(agent_name)
        current_run = None
        historical = []
        for r in all_runs:
            if current_run_id is not None and r["id"] == current_run_id:
                current_run = r
            else:
                historical.append(r)

        historical_counts = [
            len(await self._repository.list_tool_calls_for_run(r["id"])) for r in historical
        ]
        current_count = None
        if current_run is not None:
            current_count = len(await self._repository.list_tool_calls_for_run(current_run["id"]))

        return build_behavior_fingerprint(
            agent_name,
            historical_runs=historical,
            historical_tool_call_counts=historical_counts,
            current_run=current_run,
            current_tool_call_count=current_count,
        )
