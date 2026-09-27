"""Judge/Evaluator Reliability Profiles — Phase 9, "evaluation of the
evaluators" (design doc §10's judge-reliability gap, generalized here
to any MetricEvaluator). Mirrors ToolProfile/ModelProfile exactly:
sample-size-gated, computed FRESH by re-running the evaluator against
its own JudgeCalibrationExample history and comparing to the stored
human label — never trusting a stored prediction, since a metric's
implementation may have changed since the calibration example was
first labeled.
"""
from __future__ import annotations

from typing import Any

from ..evaluation.evaluators.base import EvalCase, MetricEvaluator
from ..models import JudgeProfile

MIN_SAMPLE_SIZE = 5
"""Fewer than this many calibration examples and agreement is reported
INSUFFICIENT_DATA rather than a misleading rate — same threshold as
ToolProfile/ModelProfile."""

RELIABLE_AGREEMENT = 0.85
DEGRADED_AGREEMENT = 0.6
"""agreement_rate >= RELIABLE_AGREEMENT -> RELIABLE;
>= DEGRADED_AGREEMENT -> DEGRADED; below -> UNRELIABLE."""

_SCORE_AGREEMENT_TOLERANCE = 0.15


async def build_judge_profile(
    metric: str, examples: list[dict[str, Any]], evaluator: MetricEvaluator
) -> JudgeProfile:
    n = len(examples)
    if n < MIN_SAMPLE_SIZE:
        return JudgeProfile(metric=metric, sample_count=n, reliability="INSUFFICIENT_DATA", min_sample_size=MIN_SAMPLE_SIZE)

    agreements: list[int] = []
    abs_errors: list[float] = []
    for ex in examples:
        human_score = (ex.get("human_label") or {}).get("score")
        if human_score is None:
            continue
        case = EvalCase(input=ex.get("case_input"), actual_output=ex.get("case_actual_output"))
        result = await evaluator.evaluate(case)
        if not result.available or result.score is None:
            continue
        error = abs(result.score - human_score)
        abs_errors.append(error)
        agreements.append(1 if error <= _SCORE_AGREEMENT_TOLERANCE else 0)

    if not agreements:
        return JudgeProfile(metric=metric, sample_count=n, reliability="INSUFFICIENT_DATA", min_sample_size=MIN_SAMPLE_SIZE)

    agreement_rate = sum(agreements) / len(agreements)
    mean_absolute_error = sum(abs_errors) / len(abs_errors)

    if agreement_rate >= RELIABLE_AGREEMENT:
        reliability = "RELIABLE"
    elif agreement_rate >= DEGRADED_AGREEMENT:
        reliability = "DEGRADED"
    else:
        reliability = "UNRELIABLE"

    return JudgeProfile(
        metric=metric,
        sample_count=n,
        agreement_rate=agreement_rate,
        mean_absolute_error=mean_absolute_error,
        reliability=reliability,
        min_sample_size=MIN_SAMPLE_SIZE,
    )


class JudgeProfileEngine:
    def __init__(self, repository: Any, evaluator_registry: dict[str, MetricEvaluator]) -> None:
        self._repository = repository
        self._evaluator_registry = evaluator_registry

    async def profile(self, metric: str, *, workspace_id: str | None = None) -> JudgeProfile:
        examples = await self._repository.list_judge_calibration_examples(metric, workspace_id=workspace_id)
        evaluator = self._evaluator_registry.get(metric)
        if evaluator is None:
            return JudgeProfile(
                metric=metric, sample_count=len(examples), reliability="INSUFFICIENT_DATA", min_sample_size=MIN_SAMPLE_SIZE
            )
        return await build_judge_profile(metric, examples, evaluator)
