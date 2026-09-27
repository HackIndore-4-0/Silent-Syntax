"""TrajectoryEvaluator — Phase 6 (design doc §8): agent trajectory
diagnosis built on the SAME causal-diff discipline as
agentguard.reliability.root_cause — deterministic checks first, an
optional LLM judge only for the genuinely subjective residue a
deterministic check cannot decide.

Deterministic findings (always computed, never require a judge call):
- duplicate_tool_call: the same (name, input) pair recorded more than
  once among the run's `kind="function"` trace steps.
- missing_recovery: a step that failed with no later step of the same
  name succeeding in the same trajectory.
- step_count_anomaly: the run's total step count is a statistical
  outlier vs. a caller-supplied historical baseline
  (compute_step_count_baseline() below computes one from real prior
  runs of the same agent — never invented).

A deterministic score is always available (a documented, explicit
formula over the findings' severities — never a black box); `judge`,
when supplied, can override it with a real judgment, but its absence
never blocks a result the way a genuinely-required judge call would.
"""
from __future__ import annotations

import json
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ...models import EvaluationResult
from ...storage.repository import RunRepository
from .base import EvalCase, MetricEvaluator

TrajectoryFindingKind = Literal["duplicate_tool_call", "missing_recovery", "step_count_anomaly"]

MIN_BASELINE_SAMPLE_SIZE = 5
"""Same threshold as ToolProfile/ModelProfile — fewer prior runs than
this and a step-count baseline is not meaningful (see
compute_step_count_baseline)."""

_FINDING_WEIGHT: dict[TrajectoryFindingKind, float] = {
    "duplicate_tool_call": 0.15,
    "missing_recovery": 0.35,
    "step_count_anomaly": 0.2,
}


@dataclass
class TrajectoryFinding:
    kind: TrajectoryFindingKind
    detail: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"


@dataclass
class TrajectoryJudgeVerdict:
    score: float
    reason: str = ""


TrajectoryJudgeFn = Callable[[EvalCase, list[TrajectoryFinding]], Awaitable[TrajectoryJudgeVerdict]]


def _step_key(step: dict[str, Any]) -> tuple[str | None, str]:
    return step.get("name"), json.dumps(step.get("input"), sort_keys=True, default=str)


def _duplicate_call_findings(steps: list[dict[str, Any]]) -> list[TrajectoryFinding]:
    counts: dict[tuple[str | None, str], int] = {}
    for step in steps:
        if step.get("kind") != "function":
            continue
        key = _step_key(step)
        counts[key] = counts.get(key, 0) + 1
    return [
        TrajectoryFinding(
            kind="duplicate_tool_call",
            detail=f"'{name}' called {count} times with identical input",
            severity="low",
        )
        for (name, _), count in counts.items()
        if count > 1
    ]


def _missing_recovery_findings(steps: list[dict[str, Any]]) -> list[TrajectoryFinding]:
    failed_names: set[str] = set()
    recovered_names: set[str] = set()
    for step in steps:
        name = step.get("name")
        if step.get("outcome") == "failure":
            failed_names.add(name)
        elif step.get("outcome") == "success" and name in failed_names:
            recovered_names.add(name)
    return [
        TrajectoryFinding(
            kind="missing_recovery",
            detail=f"'{name}' failed with no later successful retry in this trajectory",
            severity="high",
        )
        for name in sorted(failed_names - recovered_names, key=lambda n: n or "")
    ]


def _step_count_anomaly_finding(
    steps: list[dict[str, Any]], baseline_mean: float | None, baseline_std: float | None, threshold: float
) -> TrajectoryFinding | None:
    if baseline_mean is None or not baseline_std:
        return None
    z = (len(steps) - baseline_mean) / baseline_std
    if abs(z) <= threshold:
        return None
    return TrajectoryFinding(
        kind="step_count_anomaly",
        detail=f"{len(steps)} steps vs. historical baseline mean {baseline_mean:.1f} (z-score {z:.2f})",
        severity="medium",
    )


async def compute_step_count_baseline(
    repository: RunRepository, agent_name: str, *, workspace_id: str | None = None, limit: int = 200
) -> tuple[float | None, float | None, int]:
    """Real historical step-count baseline for `agent_name`, computed
    fresh from prior runs' actual TraceStep counts — never invented.
    Returns (mean, stdev, sample_count); mean/stdev are None when fewer
    than MIN_BASELINE_SAMPLE_SIZE prior runs exist."""
    runs = await repository.list_runs_by_agent(agent_name, limit=limit, workspace_id=workspace_id)
    counts = [len(await repository.list_trace_steps_for_run(run["id"])) for run in runs]
    if len(counts) < MIN_BASELINE_SAMPLE_SIZE:
        return None, None, len(counts)
    mean = statistics.mean(counts)
    stdev = statistics.pstdev(counts) if len(counts) > 1 else 0.0
    return mean, stdev, len(counts)


class TrajectoryEvaluator(MetricEvaluator):
    name = "trajectory"

    def __init__(
        self,
        *,
        judge: TrajectoryJudgeFn | None = None,
        baseline_mean_steps: float | None = None,
        baseline_std_steps: float | None = None,
        anomaly_z_threshold: float = 2.0,
    ) -> None:
        self._judge = judge
        self._baseline_mean_steps = baseline_mean_steps
        self._baseline_std_steps = baseline_std_steps
        self._anomaly_z_threshold = anomaly_z_threshold

    async def evaluate(self, case: EvalCase) -> EvaluationResult:
        steps = case.trace_steps or []
        findings = _duplicate_call_findings(steps) + _missing_recovery_findings(steps)
        anomaly = _step_count_anomaly_finding(
            steps, self._baseline_mean_steps, self._baseline_std_steps, self._anomaly_z_threshold
        )
        if anomaly is not None:
            findings.append(anomaly)

        deterministic_score = max(0.0, 1.0 - sum(_FINDING_WEIGHT.get(f.kind, 0.1) for f in findings))
        deterministic_reason = "; ".join(f.detail for f in findings) or "no findings"

        score = deterministic_score
        reason = deterministic_reason
        if self._judge is not None:
            verdict = await self._judge(case, findings)
            score = verdict.score
            reason = verdict.reason or deterministic_reason

        return EvaluationResult(
            evaluation_run_id="",
            source_run_id=case.source_run_id,
            metric=self.name,
            score=score,
            available=True,
            reason=reason,
        )
