"""Suite-scoped evaluator registry builder (agentguard/evaluation/registry.py).

Covers: resolving SuiteMetric entries into real MetricEvaluator instances
per-suite (so two suites can use the same "deepeval.*" key with different
thresholds/config without cross-contamination), non-DeepEval keys
(trajectory/handoff/handoff_chain) resolving without importing deepeval,
extra_evaluators overrides winning, and a catalog metric missing required
config raising loudly at build time rather than degrading to
available=False.
"""
from __future__ import annotations

import pytest

from agentguard.evaluation import EvaluationSuite, SuiteMetric
from agentguard.evaluation.evaluators.custom import CustomEvaluator
from agentguard.evaluation.evaluators.handoff import ChainHandoffEvaluator, HandoffEvaluator
from agentguard.evaluation.evaluators.trajectory import TrajectoryEvaluator
from agentguard.evaluation.metrics_catalog import DEEPEVAL_CATALOG, MissingMetricConfigError
from agentguard.evaluation.registry import build_default_evaluator_registry, build_suite_evaluator_registry

from ..fakes import InMemoryRunRepository


def _fake_deepeval_model():
    """Avoids a real OPENAI_API_KEY requirement — DeepEval resolves a
    default judge model eagerly at metric-construction time."""
    from deepeval.models.base_model import DeepEvalBaseLLM

    class _FakeModel(DeepEvalBaseLLM):
        def load_model(self):
            return self

        def generate(self, *a, **k):
            return "{}"

        async def a_generate(self, *a, **k):
            return "{}"

        def get_model_name(self):
            return "fake-model"

    return _FakeModel()


class TestBuildSuiteEvaluatorRegistry:
    def test_deepeval_key_resolves_with_the_suites_own_threshold(self):
        pytest.importorskip("deepeval")
        from agentguard.evaluation.evaluators.deepeval_adapter import DeepEvalEvaluator

        suite = EvaluationSuite(name="rag", metrics=[SuiteMetric(evaluator="deepeval.faithfulness", threshold=0.8)])

        registry = build_suite_evaluator_registry(suite, default_model=_fake_deepeval_model())

        evaluator = registry["deepeval.faithfulness"]
        assert isinstance(evaluator, DeepEvalEvaluator)
        assert evaluator._metric.threshold == 0.8

    def test_two_suites_with_the_same_key_get_independent_thresholds(self):
        pytest.importorskip("deepeval")
        suite_a = EvaluationSuite(name="a", metrics=[SuiteMetric(evaluator="deepeval.faithfulness", threshold=0.9)])
        suite_b = EvaluationSuite(name="b", metrics=[SuiteMetric(evaluator="deepeval.faithfulness", threshold=0.5)])

        registry_a = build_suite_evaluator_registry(suite_a, default_model=_fake_deepeval_model())
        registry_b = build_suite_evaluator_registry(suite_b, default_model=_fake_deepeval_model())

        assert registry_a["deepeval.faithfulness"]._metric.threshold == 0.9
        assert registry_b["deepeval.faithfulness"]._metric.threshold == 0.5

    def test_non_deepeval_keys_resolve_without_importing_deepeval(self):
        suite = EvaluationSuite(
            name="ops",
            metrics=[SuiteMetric(evaluator="trajectory"), SuiteMetric(evaluator="handoff"), SuiteMetric(evaluator="handoff_chain")],
        )
        repo = InMemoryRunRepository()

        registry = build_suite_evaluator_registry(suite, repository=repo)

        assert isinstance(registry["trajectory"], TrajectoryEvaluator)
        assert isinstance(registry["handoff"], HandoffEvaluator)
        assert isinstance(registry["handoff_chain"], ChainHandoffEvaluator)

    def test_extra_evaluators_override_a_catalog_key(self):
        pytest.importorskip("deepeval")
        suite = EvaluationSuite(name="custom", metrics=[SuiteMetric(evaluator="deepeval.faithfulness")])
        sentinel = CustomEvaluator("deepeval.faithfulness", lambda case: None)

        registry = build_suite_evaluator_registry(
            suite, default_model=_fake_deepeval_model(), extra_evaluators={"deepeval.faithfulness": sentinel}
        )

        assert registry["deepeval.faithfulness"] is sentinel

    def test_unresolvable_key_is_simply_omitted(self):
        suite = EvaluationSuite(name="custom_only", metrics=[SuiteMetric(evaluator="custom.not_registered")])

        registry = build_suite_evaluator_registry(suite)

        assert "custom.not_registered" not in registry

    def test_catalog_key_missing_required_config_raises_at_build_time(self):
        pytest.importorskip("deepeval")
        suite = EvaluationSuite(name="geval_suite", metrics=[SuiteMetric(evaluator="deepeval.geval", config={})])

        with pytest.raises(MissingMetricConfigError):
            build_suite_evaluator_registry(suite)


class TestPublicExports:
    def test_registry_builders_are_exported_from_the_evaluation_package(self):
        from agentguard.evaluation import (
            build_default_evaluator_registry as exported_default,
            build_suite_evaluator_registry as exported_suite,
        )

        assert exported_suite is build_suite_evaluator_registry
        assert exported_default is build_default_evaluator_registry


class TestBuildDefaultEvaluatorRegistry:
    def test_returns_an_entry_for_every_config_free_catalog_key_plus_trajectory_and_handoff(self):
        """Catalog keys with required_config (deepeval.geval, .json_correctness,
        .non_advice, .misuse) cannot be constructed generically — no default
        exists for their required kwargs. build_default_evaluator_registry()
        skips those (a suite author must build them explicitly, e.g. via
        extra_evaluators) and includes every other catalog key."""
        pytest.importorskip("deepeval")
        repo = InMemoryRunRepository()

        registry = build_default_evaluator_registry(repository=repo, default_model=_fake_deepeval_model())

        for key, spec in DEEPEVAL_CATALOG.items():
            if spec.required_config:
                assert key not in registry
            else:
                assert key in registry
        assert "trajectory" in registry
        assert "handoff" in registry
        assert "handoff_chain" in registry
