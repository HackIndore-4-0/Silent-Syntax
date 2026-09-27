"""JudgeProfileEngine (agentguard/reliability/judge_profile.py) —
"evaluation of the evaluators." Covers: insufficient-data gating (both
"no calibration examples registered" and "fewer than MIN_SAMPLE_SIZE"),
a genuinely reliable evaluator classified RELIABLE, a genuinely
unreliable one classified UNRELIABLE, and that the profile is computed
by actually RE-RUNNING the evaluator (not trusting a stored prediction).
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation import CustomEvaluator, EvalCase
from agentguard.models import EvaluationResult, JudgeCalibrationExample
from agentguard.reliability.judge_profile import MIN_SAMPLE_SIZE, JudgeProfileEngine

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _seed_examples(repo, metric, n, *, human_score=0.9):
    for i in range(n):
        await repo.save_judge_calibration_example(
            JudgeCalibrationExample(
                metric=metric, case_input=f"q{i}", case_actual_output=f"a{i}", human_label={"score": human_score}
            )
        )


def _accurate_evaluator():
    # Always predicts exactly the human label it was calibrated
    # against (0.9), simulating a genuinely good judge.
    async def fn(case: EvalCase) -> EvaluationResult:
        return EvaluationResult(evaluation_run_id="", metric="m", score=0.9, reason="stub")

    return CustomEvaluator("m", fn)


def _inaccurate_evaluator():
    async def fn(case: EvalCase) -> EvaluationResult:
        return EvaluationResult(evaluation_run_id="", metric="m", score=0.1, reason="stub")

    return CustomEvaluator("m", fn)


class TestJudgeProfileEngine:
    async def test_no_calibration_examples_is_insufficient_data(self, fake_repository):
        engine = JudgeProfileEngine(fake_repository, {"m": _accurate_evaluator()})
        profile = await engine.profile("m")
        assert profile.reliability == "INSUFFICIENT_DATA"
        assert profile.sample_count == 0

    async def test_fewer_than_minimum_samples_is_insufficient_data(self, fake_repository):
        await _seed_examples(fake_repository, "m", MIN_SAMPLE_SIZE - 1)
        engine = JudgeProfileEngine(fake_repository, {"m": _accurate_evaluator()})
        profile = await engine.profile("m")
        assert profile.reliability == "INSUFFICIENT_DATA"

    async def test_unregistered_metric_is_insufficient_data_not_a_crash(self, fake_repository):
        await _seed_examples(fake_repository, "m", MIN_SAMPLE_SIZE)
        engine = JudgeProfileEngine(fake_repository, {})
        profile = await engine.profile("m")
        assert profile.reliability == "INSUFFICIENT_DATA"

    async def test_accurate_evaluator_is_reliable(self, fake_repository):
        await _seed_examples(fake_repository, "m", MIN_SAMPLE_SIZE, human_score=0.9)
        engine = JudgeProfileEngine(fake_repository, {"m": _accurate_evaluator()})

        profile = await engine.profile("m")

        assert profile.reliability == "RELIABLE"
        assert profile.agreement_rate == 1.0
        assert profile.mean_absolute_error == pytest.approx(0.0)

    async def test_inaccurate_evaluator_is_unreliable(self, fake_repository):
        await _seed_examples(fake_repository, "m", MIN_SAMPLE_SIZE, human_score=0.9)
        engine = JudgeProfileEngine(fake_repository, {"m": _inaccurate_evaluator()})

        profile = await engine.profile("m")

        assert profile.reliability == "UNRELIABLE"
        assert profile.agreement_rate == 0.0
        assert profile.mean_absolute_error == pytest.approx(0.8)
