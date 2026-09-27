"""TrajectoryEvaluator (agentguard/evaluation/evaluators/trajectory.py).

Covers: duplicate-tool-call and missing-recovery deterministic
findings, the step-count-anomaly check against a supplied baseline, the
documented deterministic scoring formula, a judge override, and
compute_step_count_baseline()'s real repository-driven computation
(including the < MIN_BASELINE_SAMPLE_SIZE -> None case).
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation import EvalCase, TrajectoryJudgeVerdict
from agentguard.evaluation.evaluators.trajectory import MIN_BASELINE_SAMPLE_SIZE, TrajectoryEvaluator, compute_step_count_baseline
from agentguard.models import Run, RunStatus, TraceStep

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


def _step(name, outcome="success", input_=None, run_id="run-1"):
    return {"run_id": run_id, "kind": "function", "name": name, "input": input_ or {}, "output": None, "outcome": outcome}


class TestDeterministicFindings:
    async def test_no_findings_scores_1_0(self):
        evaluator = TrajectoryEvaluator()
        case = EvalCase(input="q", actual_output="a", trace_steps=[_step("a"), _step("b")])

        result = await evaluator.evaluate(case)

        assert result.score == 1.0
        assert result.reason == "no findings"

    async def test_duplicate_tool_call_lowers_score_and_is_named_in_reason(self):
        evaluator = TrajectoryEvaluator()
        steps = [_step("search", input_={"q": "x"}), _step("search", input_={"q": "x"})]
        case = EvalCase(input="q", actual_output="a", trace_steps=steps)

        result = await evaluator.evaluate(case)

        assert result.score == pytest.approx(0.85)  # 1.0 - 0.15 duplicate weight
        assert "called 2 times" in result.reason

    async def test_missing_recovery_lowers_score_more_than_a_duplicate(self):
        evaluator = TrajectoryEvaluator()
        steps = [_step("charge_card", outcome="failure")]
        case = EvalCase(input="q", actual_output="a", trace_steps=steps)

        result = await evaluator.evaluate(case)

        assert result.score == pytest.approx(0.65)  # 1.0 - 0.35 missing-recovery weight
        assert "failed with no later successful retry" in result.reason

    async def test_a_failure_followed_by_a_later_success_is_not_flagged_as_missing_recovery(self):
        # Same name+input recorded twice IS still a duplicate_tool_call
        # finding (a real, separate fact) — this test only asserts that
        # missing_recovery specifically does not ALSO fire here.
        evaluator = TrajectoryEvaluator()
        steps = [_step("charge_card", outcome="failure"), _step("charge_card", outcome="success")]
        case = EvalCase(input="q", actual_output="a", trace_steps=steps)

        result = await evaluator.evaluate(case)

        assert "failed with no later successful retry" not in result.reason
        assert result.score == pytest.approx(0.85)  # only the duplicate-call weight applies


class TestStepCountAnomaly:
    async def test_step_count_far_from_baseline_is_flagged(self):
        evaluator = TrajectoryEvaluator(baseline_mean_steps=5.0, baseline_std_steps=1.0, anomaly_z_threshold=2.0)
        steps = [_step(f"step_{i}") for i in range(20)]
        case = EvalCase(input="q", actual_output="a", trace_steps=steps)

        result = await evaluator.evaluate(case)

        assert "vs. historical baseline" in result.reason
        assert result.score < 1.0

    async def test_step_count_within_baseline_is_not_flagged(self):
        evaluator = TrajectoryEvaluator(baseline_mean_steps=5.0, baseline_std_steps=2.0, anomaly_z_threshold=2.0)
        steps = [_step(f"step_{i}") for i in range(6)]
        case = EvalCase(input="q", actual_output="a", trace_steps=steps)

        result = await evaluator.evaluate(case)

        assert result.score == 1.0

    async def test_no_baseline_supplied_never_flags_anomaly(self):
        evaluator = TrajectoryEvaluator()
        steps = [_step(f"step_{i}") for i in range(50)]
        case = EvalCase(input="q", actual_output="a", trace_steps=steps)

        result = await evaluator.evaluate(case)

        assert result.score == 1.0


class TestJudgeOverride:
    async def test_judge_result_overrides_the_deterministic_score(self):
        async def judge(case, findings):
            return TrajectoryJudgeVerdict(score=0.42, reason="a human reviewer would flag this")

        evaluator = TrajectoryEvaluator(judge=judge)
        case = EvalCase(input="q", actual_output="a", trace_steps=[_step("a")])

        result = await evaluator.evaluate(case)

        assert result.score == 0.42
        assert result.reason == "a human reviewer would flag this"


class TestComputeStepCountBaseline:
    async def test_fewer_than_minimum_samples_returns_none(self):
        repo = InMemoryRunRepository()
        run = Run(agent_name="agent_x", status=RunStatus.STOP)
        await repo.create_run(run)

        mean, std, count = await compute_step_count_baseline(repo, "agent_x")

        assert mean is None
        assert std is None
        assert count == 1
        assert count < MIN_BASELINE_SAMPLE_SIZE

    async def test_enough_samples_computes_a_real_mean_and_stdev(self):
        repo = InMemoryRunRepository()
        for step_count in (3, 5, 5, 7, 5):
            run = Run(agent_name="agent_y", status=RunStatus.STOP)
            await repo.create_run(run)
            for i in range(step_count):
                await repo.save_trace_step(TraceStep(run_id=run.id, kind="function", name=f"s{i}", outcome="success"))

        mean, std, count = await compute_step_count_baseline(repo, "agent_y")

        assert count == 5
        assert mean == pytest.approx(5.0)
        assert std is not None
