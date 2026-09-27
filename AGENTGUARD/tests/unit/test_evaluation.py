"""Evaluation Platform, Phase 1 (agentguard/evaluation/).

Covers: MetricEvaluator/CustomEvaluator dispatch, EvalCase construction
from real Run/TraceStep data (build_eval_case_from_run), threshold-based
pass/fail, an unregistered metric producing an explicit available=False
result rather than a silent skip or a crash, and EvaluationSuite/Run/
Result persistence (including workspace scoping) via InMemoryRunRepository.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation import (
    CustomEvaluator,
    EvalCase,
    EvaluationEngine,
    EvaluationSuite,
    SuiteMetric,
    build_eval_case_from_run,
)
from agentguard.models import EvaluationResult, Run, RunStatus, TraceStep

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _make_run(repo: InMemoryRunRepository, *, task: str, final_state: dict, workspace_id=None) -> str:
    run = Run(
        agent_name="test_agent", task=task, status=RunStatus.STOP,
        final_state=final_state, workspace_id=workspace_id,
    )
    await repo.create_run(run)
    return run.id


def _always_scores(name: str, score: float, *, available: bool = True):
    async def fn(case: EvalCase) -> EvaluationResult:
        return EvaluationResult(
            evaluation_run_id="", source_run_id=case.source_run_id,
            metric=name, score=score, available=available, reason="stub",
        )

    return CustomEvaluator(name, fn)


class TestBuildEvalCaseFromRun:
    async def test_builds_case_from_task_and_final_state(self, fake_repository):
        run_id = await _make_run(fake_repository, task="what is the refund policy?", final_state={"answer": "30 days"})
        run = await fake_repository.get_run(run_id)

        case = build_eval_case_from_run(run)

        assert case.input == "what is the refund policy?"
        assert case.actual_output == {"answer": "30 days"}
        assert case.source_run_id == run_id
        assert case.retrieval_context is None
        assert case.tools_called is None
        assert case.source_step_id is None  # no trace steps at all in this run

    async def test_source_step_id_anchors_to_the_last_llm_call_step(self, fake_repository):
        run_id = await _make_run(fake_repository, task="q", final_state={"answer": "a"})
        await fake_repository.save_trace_step(
            TraceStep(run_id=run_id, kind="llm_call", name="c1", outcome="success", model_name="gpt-4o-mini")
        )
        last = TraceStep(run_id=run_id, kind="llm_call", name="c2", outcome="success", model_name="gpt-4o-mini")
        await fake_repository.save_trace_step(last)
        run = await fake_repository.get_run(run_id)
        steps = await fake_repository.list_trace_steps_for_run(run_id)

        case = build_eval_case_from_run(run, steps)

        assert case.source_step_id == last.id

    async def test_retrieval_shaped_step_populates_retrieval_context(self, fake_repository):
        run_id = await _make_run(fake_repository, task="q", final_state={"answer": "a"})
        await fake_repository.save_trace_step(
            TraceStep(run_id=run_id, kind="function", name="search_policies", output="policy doc text", outcome="success")
        )
        await fake_repository.save_trace_step(
            TraceStep(run_id=run_id, kind="function", name="format_answer", output="a", outcome="success")
        )
        run = await fake_repository.get_run(run_id)
        steps = await fake_repository.list_trace_steps_for_run(run_id)

        case = build_eval_case_from_run(run, steps)

        assert case.retrieval_context == ["policy doc text"]
        assert {t["name"] for t in case.tools_called} == {"search_policies", "format_answer"}


class TestEvaluationEngine:
    async def test_custom_evaluator_produces_a_persisted_result(self, fake_repository):
        run_id = await _make_run(fake_repository, task="q", final_state={"answer": "a"})
        suite = EvaluationSuite(name="basic", metrics=[SuiteMetric(evaluator="custom.always_high")])
        engine = EvaluationEngine(fake_repository, {"custom.always_high": _always_scores("custom.always_high", 0.9)})

        case = EvalCase(input="q", actual_output={"answer": "a"}, source_run_id=run_id)
        evaluation_run = await engine.run_suite(suite, [case])

        assert evaluation_run.status == "complete"
        assert evaluation_run.finished_at is not None
        results = await fake_repository.list_evaluation_results(evaluation_run.id)
        assert len(results) == 1
        assert results[0]["metric"] == "custom.always_high"
        assert results[0]["score"] == 0.9
        assert results[0]["source_run_id"] == run_id
        assert results[0]["evaluation_run_id"] == evaluation_run.id

    async def test_threshold_determines_passed(self, fake_repository):
        suite = EvaluationSuite(
            name="thresholded",
            metrics=[SuiteMetric(evaluator="custom.borderline", threshold=0.8)],
        )
        engine = EvaluationEngine(fake_repository, {"custom.borderline": _always_scores("custom.borderline", 0.5)})

        evaluation_run = await engine.run_suite(suite, [EvalCase(input="q", actual_output="a")])

        results = await fake_repository.list_evaluation_results(evaluation_run.id)
        assert results[0]["passed"] is False

    async def test_unregistered_metric_is_explicitly_unavailable_not_skipped(self, fake_repository):
        suite = EvaluationSuite(name="missing", metrics=[SuiteMetric(evaluator="deepeval.faithfulness")])
        engine = EvaluationEngine(fake_repository, {})

        evaluation_run = await engine.run_suite(suite, [EvalCase(input="q", actual_output="a")])

        results = await fake_repository.list_evaluation_results(evaluation_run.id)
        assert len(results) == 1
        assert results[0]["available"] is False
        assert "no evaluator registered" in results[0]["reason"]

    async def test_multiple_metrics_and_cases_each_produce_a_result_row(self, fake_repository):
        suite = EvaluationSuite(
            name="matrix",
            metrics=[SuiteMetric(evaluator="custom.a"), SuiteMetric(evaluator="custom.b")],
        )
        engine = EvaluationEngine(
            fake_repository, {"custom.a": _always_scores("custom.a", 0.1), "custom.b": _always_scores("custom.b", 0.2)}
        )
        cases = [EvalCase(input="q1", actual_output="a1"), EvalCase(input="q2", actual_output="a2")]

        evaluation_run = await engine.run_suite(suite, cases)

        results = await fake_repository.list_evaluation_results(evaluation_run.id)
        assert len(results) == 4  # 2 metrics x 2 cases


class TestEvaluationSuitePersistenceAndWorkspaceScoping:
    async def test_suite_round_trips_through_the_repository(self, fake_repository):
        suite = EvaluationSuite(
            name="rag_defaults",
            app_type="rag",
            metrics=[SuiteMetric(evaluator="custom.faithfulness", threshold=0.8, recommended_by="auto", reason="retrieval-shaped traces detected")],
        )
        await fake_repository.save_evaluation_suite(suite)

        fetched = await fake_repository.get_evaluation_suite(suite.id)

        assert fetched["name"] == "rag_defaults"
        assert fetched["metrics"][0]["evaluator"] == "custom.faithfulness"
        assert fetched["metrics"][0]["recommended_by"] == "auto"

    async def test_evaluation_runs_are_scoped_per_workspace(self, fake_repository):
        suite = EvaluationSuite(name="s", metrics=[SuiteMetric(evaluator="custom.a")])
        engine = EvaluationEngine(fake_repository, {"custom.a": _always_scores("custom.a", 0.5)})

        await engine.run_suite(suite, [EvalCase(input="q", actual_output="a")], workspace_id="ws-alice")
        await engine.run_suite(suite, [EvalCase(input="q", actual_output="a")], workspace_id="ws-bob")

        alice_runs = await fake_repository.list_evaluation_runs(workspace_id="ws-alice")
        bob_runs = await fake_repository.list_evaluation_runs(workspace_id="ws-bob")

        assert len(alice_runs) == 1
        assert len(bob_runs) == 1
        assert alice_runs[0]["id"] != bob_runs[0]["id"]
