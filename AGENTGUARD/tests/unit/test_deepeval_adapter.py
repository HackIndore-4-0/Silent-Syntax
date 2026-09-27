"""DeepEvalEvaluator (agentguard/evaluation/evaluators/deepeval_adapter.py).

Uses hand-built fake `deepeval.metrics.BaseMetric` subclasses — never a
real judge-model call/API key — to test the adapter's own glue code
(EvalCase -> LLMTestCase construction, result mapping, error handling)
without depending on DeepEval's own metric correctness or network
access. Skipped entirely when the optional `deepeval` extra isn't
installed, mirroring test_litellm_gateway.py's pytest.importorskip
pattern for `litellm`.
"""
from __future__ import annotations

import pytest

pytest.importorskip("deepeval")

import agentguard
from agentguard.evaluation import EvalCase, EvaluationEngine, EvaluationSuite, SuiteMetric
from agentguard.evaluation.evaluators.deepeval_adapter import DeepEvalEvaluator

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


def _fake_metric_class():
    from deepeval.metrics.base_metric import BaseMetric

    class _FakeMetric(BaseMetric):
        def __init__(self, *, score=0.87, reason="looks faithful", success=True, raises=None):
            self.threshold = 0.5
            self.score = score
            self.reason = reason
            self.success = success
            self.evaluation_model = "gpt-4o-mini"
            self.evaluation_cost = 0.002
            self.error = None
            self._raises = raises
            self.received_test_case = None

        def measure(self, test_case, *args, **kwargs):
            raise NotImplementedError

        async def a_measure(self, test_case, *args, **kwargs):
            self.received_test_case = test_case
            if self._raises is not None:
                raise self._raises
            return self.score

    return _FakeMetric


class TestDeepEvalEvaluator:
    async def test_successful_measure_maps_to_evaluation_result(self):
        FakeMetric = _fake_metric_class()
        metric = FakeMetric(score=0.91, reason="all claims supported", success=True)
        evaluator = DeepEvalEvaluator("deepeval.faithfulness", metric)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id="run-1"))

        assert result.available is True
        assert result.score == 0.91
        assert result.passed is True
        assert result.reason == "all claims supported"
        assert result.judge_model == "gpt-4o-mini"
        assert result.cost_usd == 0.002
        assert result.metric == "deepeval.faithfulness"
        assert result.source_run_id == "run-1"

    async def test_metric_exception_is_reported_as_unavailable_not_raised(self):
        FakeMetric = _fake_metric_class()
        metric = FakeMetric(raises=RuntimeError("judge model unreachable"))
        evaluator = DeepEvalEvaluator("deepeval.faithfulness", metric)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a"))

        assert result.available is False
        assert result.score is None
        assert "judge model unreachable" in result.reason

    async def test_eval_case_is_converted_to_a_well_formed_llm_test_case(self):
        FakeMetric = _fake_metric_class()
        metric = FakeMetric()
        evaluator = DeepEvalEvaluator("deepeval.faithfulness", metric)

        case = EvalCase(
            input="what is the refund window?",
            actual_output={"answer": "30 days"},
            expected_output="30 days",
            retrieval_context=["policy: refunds within 30 days"],
            tools_called=[{"name": "search_policies", "input": {"q": "refund"}, "output": "30 days"}],
            source_run_id="run-2",
        )
        await evaluator.evaluate(case)

        tc = metric.received_test_case
        assert tc.input == "what is the refund window?"
        assert tc.actual_output == "{'answer': '30 days'}"
        assert tc.expected_output == "30 days"
        assert tc.retrieval_context == ["policy: refunds within 30 days"]
        assert len(tc.tools_called) == 1
        assert tc.tools_called[0].name == "search_policies"
        assert tc.tools_called[0].output == "30 days"

    async def test_threshold_missing_score_is_unavailable_via_engine_dispatch(self):
        FakeMetric = _fake_metric_class()
        suite = EvaluationSuite(name="rag", metrics=[SuiteMetric(evaluator="deepeval.faithfulness", threshold=0.8)])
        engine = EvaluationEngine(
            agentguard._runtime.get_repository(),
            {"deepeval.faithfulness": DeepEvalEvaluator("deepeval.faithfulness", FakeMetric(score=0.95))},
        )

        evaluation_run = await engine.run_suite(suite, [EvalCase(input="q", actual_output="a")])

        results = await agentguard._runtime.get_repository().list_evaluation_results(evaluation_run.id)
        assert results[0]["score"] == 0.95
        assert results[0]["passed"] is True
