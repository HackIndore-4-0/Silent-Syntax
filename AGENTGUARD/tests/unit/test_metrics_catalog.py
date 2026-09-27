"""DeepEval metric catalog (agentguard/evaluation/metrics_catalog.py).

Covers: building a real DeepEvalEvaluator from a catalog key + kwargs,
an unknown key raising loudly rather than silently skipping, a metric
missing required non-threshold constructor config raising a named error,
and the catalog listing helper never importing `deepeval` at module scope
(so `import agentguard.evaluation` never requires the optional extra).
"""
from __future__ import annotations

import ast
import inspect

import pytest

pytest.importorskip("deepeval")

from agentguard.evaluation.evaluators.deepeval_adapter import DeepEvalEvaluator
from agentguard.evaluation.metrics_catalog import (
    MissingMetricConfigError,
    UnknownMetricError,
    build_deepeval_evaluator,
    list_deepeval_catalog,
)


def _fake_deepeval_model():
    """A minimal DeepEvalBaseLLM so metric construction never needs a real
    OPENAI_API_KEY (DeepEval resolves a default judge model eagerly at
    metric-construction time when no `model=` is given)."""
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


class TestBuildDeepEvalEvaluator:
    def test_valid_key_builds_a_working_evaluator_wrapping_the_right_class(self):
        from deepeval.metrics import HallucinationMetric

        evaluator = build_deepeval_evaluator("deepeval.hallucination", threshold=0.6, model=_fake_deepeval_model())

        assert isinstance(evaluator, DeepEvalEvaluator)
        assert isinstance(evaluator._metric, HallucinationMetric)
        assert evaluator._metric.threshold == 0.6

    def test_unknown_key_raises_unknown_metric_error(self):
        with pytest.raises(UnknownMetricError):
            build_deepeval_evaluator("deepeval.nonexistent_metric")

    def test_missing_required_config_raises_naming_the_missing_kwarg(self):
        with pytest.raises(MissingMetricConfigError) as exc_info:
            build_deepeval_evaluator("deepeval.geval", model=_fake_deepeval_model())

        assert "evaluation_steps" in str(exc_info.value)

    def test_required_config_supplied_succeeds(self):
        from deepeval.metrics import GEval

        evaluator = build_deepeval_evaluator(
            "deepeval.geval",
            name="Tone",
            evaluation_steps=["Check the tone is professional"],
            model=_fake_deepeval_model(),
        )

        assert isinstance(evaluator._metric, GEval)


class TestPublicExports:
    def test_catalog_symbols_are_exported_from_the_evaluation_package(self):
        from agentguard.evaluation import (
            DEEPEVAL_CATALOG,
            MissingMetricConfigError as ExportedMissing,
            UnknownMetricError as ExportedUnknown,
            build_deepeval_evaluator as exported_builder,
            list_deepeval_catalog as exported_lister,
        )

        assert exported_builder is build_deepeval_evaluator
        assert exported_lister is list_deepeval_catalog
        assert ExportedUnknown is UnknownMetricError
        assert ExportedMissing is MissingMetricConfigError
        assert "deepeval.faithfulness" in DEEPEVAL_CATALOG


class TestListDeepEvalCatalog:
    def test_returns_metadata_for_known_keys(self):
        catalog = list_deepeval_catalog()
        keys = {entry["key"] for entry in catalog}

        assert "deepeval.faithfulness" in keys
        assert "deepeval.hallucination" in keys
        entry = next(e for e in catalog if e["key"] == "deepeval.faithfulness")
        assert entry["category"] == "rag"
        assert entry["reference_based"] is False

    def test_module_has_no_top_level_deepeval_import(self):
        """The optional `deepeval` extra must stay optional at import time —
        verified structurally (works even when deepeval IS installed, as it
        is in this test environment) rather than by uninstalling it."""
        from agentguard.evaluation import metrics_catalog

        source = inspect.getsource(metrics_catalog)
        tree = ast.parse(source)
        top_level_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]

        for node in top_level_imports:
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue  # relative import of agentguard's own code, never the third-party `deepeval` package
                module_root = (node.module or "").split(".")[0]
            else:
                module_root = node.names[0].name.split(".")[0]
            assert module_root != "deepeval"
