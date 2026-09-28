"""ModelBenchmarkEngine (agentguard/evaluation/benchmark.py).

Uses a deterministic stub `model_call_fn` (per-model canned score via a
CustomEvaluator) rather than a real provider call, consistent with this
whole test suite's "deterministic stand-in, never live network" rule.
Covers: run() persists per-model results, and recommend()'s three
supported objectives each pick the right model for a small 3-model
fixture, plus the unsupported-objective and no-eligible-candidate paths.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation import CustomEvaluator, EvalCase, EvaluationEngine, EvaluationSuite, SuiteMetric
from agentguard.evaluation.benchmark import ModelBenchmarkEngine, ModelCallResult
from agentguard.models import EvaluationResult

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


# model -> (score, cost_usd, latency_ms)
_FIXTURE = {
    "gpt-4o": (0.95, 0.08, 1200.0),
    "gpt-4o-mini": (0.88, 0.01, 400.0),
    "claude-haiku": (0.72, 0.005, 250.0),
}


def _quality_evaluator():
    # Scores off `actual_output` (which model_call_fn sets per-model),
    # never `input` — ModelBenchmarkEngine reuses the SAME EvalCase.input
    # across every model in a benchmark, so a stub keyed on input would
    # silently score every model identically.
    async def fn(case: EvalCase) -> EvaluationResult:
        model = case.actual_output.rsplit("'s answer", 1)[0]
        score, _, _ = _FIXTURE[model]
        return EvaluationResult(evaluation_run_id="", source_run_id=case.source_run_id, metric="quality", score=score, reason="stub")

    return CustomEvaluator("quality", fn)


async def _model_call_fn(model, case):
    _, cost, latency = _FIXTURE[model]
    return ModelCallResult(actual_output=f"{model}'s answer", cost_usd=cost, latency_ms=latency)


def _suite():
    return EvaluationSuite(name="bench_suite", metrics=[SuiteMetric(evaluator="quality")])


class TestModelBenchmarkRun:
    async def test_run_persists_a_result_row_per_model(self, fake_repository):
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})
        bench_engine = ModelBenchmarkEngine(fake_repository, engine, _model_call_fn)
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")

        benchmark = await bench_engine.run(_suite(), [case], list(_FIXTURE.keys()))

        results = await fake_repository.list_model_benchmark_results(benchmark.id)
        assert len(results) == 3
        assert {r["model"] for r in results} == set(_FIXTURE.keys())


class TestModelBenchmarkRunErrorIsolation:
    """Regression tests: run() used to have zero error handling around
    model_call_fn -- one failing (model, case) call aborted the entire
    benchmark, including every model that hadn't been tried yet. A
    background job retrying that failure from scratch would then
    re-spend real money re-querying every model that had already
    succeeded, only to hit the same failure again."""

    async def test_one_failing_model_does_not_abort_the_others(self, fake_repository):
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})

        async def flaky_call_fn(model, case):
            if model == "claude-haiku":
                raise RuntimeError("simulated provider outage")
            return await _model_call_fn(model, case)

        bench_engine = ModelBenchmarkEngine(fake_repository, engine, flaky_call_fn)
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")

        benchmark = await bench_engine.run(_suite(), [case], list(_FIXTURE.keys()))

        assert benchmark is not None
        results = await fake_repository.list_model_benchmark_results(benchmark.id)
        assert {r["model"] for r in results} == {"gpt-4o", "gpt-4o-mini"}

    async def test_a_failing_case_is_excluded_not_scored_as_a_bad_response(self, fake_repository):
        """A call that errors out must never end up scored as if `None`
        (or an error string) were the model's real answer -- that would
        misreport an infrastructure failure as a bad response."""
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})
        good_case = EvalCase(input="q1", actual_output=None, source_run_id="run-1")
        bad_case = EvalCase(input="q2", actual_output=None, source_run_id="run-2")

        async def flaky_call_fn(model, case):
            if case.source_run_id == "run-2":
                raise RuntimeError("simulated timeout")
            return await _model_call_fn(model, case)

        bench_engine = ModelBenchmarkEngine(fake_repository, engine, flaky_call_fn)
        benchmark = await bench_engine.run(_suite(), [good_case, bad_case], ["gpt-4o"])

        results = await fake_repository.list_model_benchmark_results(benchmark.id)
        assert len(results) == 1
        assert results[0]["example_id"] == "run-1"

    async def test_every_case_failing_for_a_model_skips_it_without_crashing(self, fake_repository):
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")

        async def always_fails(model, case):
            raise RuntimeError("down")

        bench_engine = ModelBenchmarkEngine(fake_repository, engine, always_fails)
        benchmark = await bench_engine.run(_suite(), [case], ["gpt-4o"])

        assert benchmark is not None
        assert await fake_repository.list_model_benchmark_results(benchmark.id) == []


class TestModelBenchmarkRunWithPreCreatedBenchmark:
    async def test_run_reuses_a_caller_supplied_benchmark_row(self, fake_repository):
        from agentguard.models import ModelBenchmark

        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})
        bench_engine = ModelBenchmarkEngine(fake_repository, engine, _model_call_fn)
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")

        pre_created = ModelBenchmark(suite_id=_suite().id, models=list(_FIXTURE.keys()))
        await fake_repository.save_model_benchmark(pre_created)

        returned = await bench_engine.run(_suite(), [case], list(_FIXTURE.keys()), benchmark=pre_created)

        assert returned.id == pre_created.id
        results = await fake_repository.list_model_benchmark_results(pre_created.id)
        assert len(results) == 3
        # only the one row the caller pre-created exists — run() didn't save a second one
        assert len(await fake_repository.list_model_benchmarks()) == 1


class TestModelBenchmarkRecommend:
    async def _run_benchmark(self, fake_repository):
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})
        bench_engine = ModelBenchmarkEngine(fake_repository, engine, _model_call_fn)
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")
        return bench_engine, await bench_engine.run(_suite(), [case], list(_FIXTURE.keys()))

    async def test_cheapest_above_quality_threshold(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        rec = await bench_engine.recommend(
            benchmark.id, "cheapest_above_quality_threshold", quality_metric="quality", quality_threshold=0.85
        )

        assert rec.model == "gpt-4o-mini"  # cheapest of the two >= 0.85 (gpt-4o, gpt-4o-mini)
        assert "gpt-4o" in rec.alternatives

    async def test_fastest_above_quality_threshold(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        rec = await bench_engine.recommend(
            benchmark.id, "fastest_above_quality_threshold", quality_metric="quality", quality_threshold=0.7
        )

        assert rec.model == "claude-haiku"  # fastest among all three (all clear 0.7)

    async def test_highest_quality_within_budget(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        rec = await bench_engine.recommend(
            benchmark.id, "highest_quality_within_budget", quality_metric="quality", cost_budget_usd=0.02
        )

        assert rec.model == "gpt-4o-mini"  # highest score within the 0.02 budget (gpt-4o is too expensive)

    async def test_no_eligible_candidate_reports_none_not_a_crash(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        rec = await bench_engine.recommend(
            benchmark.id, "cheapest_above_quality_threshold", quality_metric="quality", quality_threshold=0.999
        )

        assert rec.model is None
        assert "no model met" in rec.reasoning

    async def test_unsupported_objective_raises(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        with pytest.raises(ValueError, match="unsupported objective"):
            await bench_engine.recommend(benchmark.id, "best_vibes", quality_metric="quality")

    async def test_recommendation_is_persisted(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        await bench_engine.recommend(benchmark.id, "cheapest_above_quality_threshold", quality_metric="quality", quality_threshold=0.7)

        stored = await fake_repository.list_recommendations(kind="model")
        assert len(stored) == 1
        assert stored[0]["subject_id"] == benchmark.id

    async def test_recommendation_id_round_trips_to_the_persisted_row(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        rec = await bench_engine.recommend(
            benchmark.id, "cheapest_above_quality_threshold", quality_metric="quality", quality_threshold=0.7
        )

        assert rec.recommendation_id is not None
        stored = await fake_repository.get_recommendation(rec.recommendation_id)
        assert stored is not None
        assert stored["subject_id"] == benchmark.id
        assert stored["status"] == "pending"

    async def test_no_eligible_candidate_never_sets_a_recommendation_id(self, fake_repository):
        bench_engine, benchmark = await self._run_benchmark(fake_repository)

        rec = await bench_engine.recommend(
            benchmark.id, "cheapest_above_quality_threshold", quality_metric="quality", quality_threshold=0.999
        )

        assert rec.recommendation_id is None

    async def test_best_long_context_filters_by_real_token_count_then_ranks_by_quality(self, fake_repository):
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})

        async def long_context_call_fn(model, case):
            score, cost, latency = _FIXTURE[model]
            tokens = {"gpt-4o": 50_000, "gpt-4o-mini": 500, "claude-haiku": 60_000}[model]
            return ModelCallResult(actual_output=f"{model}'s answer", cost_usd=cost, latency_ms=latency, tokens_input=tokens)

        bench_engine = ModelBenchmarkEngine(fake_repository, engine, long_context_call_fn)
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")
        benchmark = await bench_engine.run(_suite(), [case], list(_FIXTURE.keys()))

        rec = await bench_engine.recommend(
            benchmark.id, "best_long_context", quality_metric="quality", min_tokens_input=10_000
        )

        # gpt-4o-mini is excluded (only 500 tokens); between gpt-4o (0.95)
        # and claude-haiku (0.72), both >= 10k tokens, gpt-4o wins on quality.
        assert rec.model == "gpt-4o"
        assert "gpt-4o-mini" not in rec.alternatives

    async def test_best_tool_calling_reliability_reuses_model_profile_engine(self, fake_repository):
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})
        bench_engine = ModelBenchmarkEngine(fake_repository, engine, _model_call_fn)
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")
        benchmark = await bench_engine.run(_suite(), [case], ["gpt-4o", "gpt-4o-mini"])

        # Seed REAL TraceStep(kind="llm_call") history so ModelProfileEngine
        # has enough samples: gpt-4o-mini reliable, gpt-4o all failures.
        from agentguard.models import TraceStep

        for _ in range(6):
            await fake_repository.save_trace_step(
                TraceStep(run_id="run-1", kind="llm_call", name="c", outcome="success", model_name="gpt-4o-mini", latency_ms=100.0)
            )
        for _ in range(6):
            await fake_repository.save_trace_step(
                TraceStep(run_id="run-1", kind="llm_call", name="c", outcome="failure", model_name="gpt-4o", latency_ms=100.0)
            )

        rec = await bench_engine.recommend(benchmark.id, "best_tool_calling_reliability")

        assert rec.model == "gpt-4o-mini"
        assert "RELIABLE" in rec.reasoning or "success_rate=1.000" in rec.reasoning

    async def test_best_tool_calling_reliability_with_no_history_reports_none(self, fake_repository):
        engine = EvaluationEngine(fake_repository, {"quality": _quality_evaluator()})
        bench_engine = ModelBenchmarkEngine(fake_repository, engine, _model_call_fn)
        case = EvalCase(input="q", actual_output=None, source_run_id="run-1")
        benchmark = await bench_engine.run(_suite(), [case], list(_FIXTURE.keys()))

        rec = await bench_engine.recommend(benchmark.id, "best_tool_calling_reliability")

        assert rec.model is None
        assert "no candidate model has enough call history" in rec.reasoning
