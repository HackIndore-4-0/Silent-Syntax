"""DeepEval metric catalog — a string-keyed registry of DeepEval's
single-turn metrics, so a `SuiteMetric(evaluator="deepeval.hallucination")`
can be resolved to a real, constructed `deepeval.metrics.BaseMetric`
instance without every caller hand-importing and hand-constructing it.

Kept separate from `evaluators/` (adapter *classes*) because this module
holds catalog *data* consumed by both `recommend.py` and (later) a
dashboard "available metrics" listing — mirrors the existing split
between `evaluators/base.py` (interfaces) and `engine.py` (data-shaping
helpers like `build_eval_case_from_run`).

Scoped to single-turn metrics only: DeepEval's conversational metrics
(ConversationCompletenessMetric, RoleAdherenceMetric, ConversationalGEval,
...) need a multi-turn `ConversationalTestCase`, but AgentGuard's `Run`
model has no session/conversation-id concept to group multiple runs into
one conversation — out of scope until that exists. `DAG`/`DAGMetric` is
also excluded: it needs a hand-authored decision graph per use case, which
is inherently manual code, not a string-keyed registry entry — use
`CustomEvaluator` directly for DAG-based scoring instead.

`deepeval` itself is imported lazily, only inside `build_deepeval_evaluator`
— this module has zero top-level `deepeval` imports, so `import agentguard`
/ `import agentguard.evaluation` never requires the optional `deepeval`
extra (`pyproject.toml`: `deepeval = ["deepeval>=2.0"]`), exactly mirroring
`DeepEvalEvaluator`'s own deferred-import pattern.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Literal

from .evaluators.deepeval_adapter import DeepEvalEvaluator

MetricCategory = Literal["rag", "agentic", "conversational", "safety", "other"]


class UnknownMetricError(Exception):
    """Raised when a catalog key has no matching DEEPEVAL_CATALOG entry —
    never a silent skip, matching EvaluationEngine's own "explicit gap"
    convention for an unresolvable evaluator."""

    def __init__(self, key: str) -> None:
        super().__init__(f"unknown deepeval metric key: {key!r}")
        self.key = key


class MissingMetricConfigError(Exception):
    """Raised when a catalog metric's required_config kwargs are not
    supplied — names exactly which ones are missing so a suite author can
    fix it immediately, rather than discovering it as a run-time score
    that's mysteriously unavailable."""

    def __init__(self, key: str, missing: tuple[str, ...]) -> None:
        super().__init__(f"deepeval metric {key!r} requires config: {', '.join(missing)}")
        self.key = key
        self.missing = missing


@dataclass(frozen=True)
class DeepEvalMetricSpec:
    key: str
    metric_class_path: str
    category: MetricCategory
    reference_based: bool
    required_config: tuple[str, ...]
    description: str


DEEPEVAL_CATALOG: dict[str, DeepEvalMetricSpec] = {
    spec.key: spec
    for spec in [
        DeepEvalMetricSpec(
            "deepeval.answer_relevancy", "deepeval.metrics.AnswerRelevancyMetric",
            "rag", False, (), "Is the output relevant to the input?",
        ),
        DeepEvalMetricSpec(
            "deepeval.faithfulness", "deepeval.metrics.FaithfulnessMetric",
            "rag", False, (), "Does the output factually align with the retrieval context?",
        ),
        DeepEvalMetricSpec(
            "deepeval.contextual_precision", "deepeval.metrics.ContextualPrecisionMetric",
            "rag", True, (), "Are the most relevant retrieved nodes ranked highest?",
        ),
        DeepEvalMetricSpec(
            "deepeval.contextual_recall", "deepeval.metrics.ContextualRecallMetric",
            "rag", True, (), "Does the retrieval context align with the expected output?",
        ),
        DeepEvalMetricSpec(
            "deepeval.contextual_relevancy", "deepeval.metrics.ContextualRelevancyMetric",
            "rag", False, (), "Is the retrieved context relevant to the input?",
        ),
        DeepEvalMetricSpec(
            "deepeval.hallucination", "deepeval.metrics.HallucinationMetric",
            "rag", False, (), "Does the output contradict the provided context?",
        ),
        DeepEvalMetricSpec(
            "deepeval.tool_correctness", "deepeval.metrics.ToolCorrectnessMetric",
            "agentic", False, (), "Were the correct tools called, in the right order?",
        ),
        DeepEvalMetricSpec(
            "deepeval.argument_correctness", "deepeval.metrics.ArgumentCorrectnessMetric",
            "agentic", False, (), "Were correct arguments passed to the called tools?",
        ),
        DeepEvalMetricSpec(
            "deepeval.task_completion", "deepeval.metrics.TaskCompletionMetric",
            "agentic", False, (), "Did the agent complete the stated task?",
        ),
        DeepEvalMetricSpec(
            "deepeval.summarization", "deepeval.metrics.SummarizationMetric",
            "other", False, (), "Is the generated summary a faithful, sufficient summary of the source?",
        ),
        DeepEvalMetricSpec(
            "deepeval.json_correctness", "deepeval.metrics.JsonCorrectnessMetric",
            "other", False, ("expected_schema",), "Does the output match a given JSON/Pydantic schema?",
        ),
        DeepEvalMetricSpec(
            "deepeval.prompt_alignment", "deepeval.metrics.PromptAlignmentMetric",
            "other", False, ("prompt_instructions",), "Does the output follow the prompt template's instructions?",
        ),
        DeepEvalMetricSpec(
            "deepeval.bias", "deepeval.metrics.BiasMetric",
            "safety", False, (), "Does the output contain biased statements?",
        ),
        DeepEvalMetricSpec(
            "deepeval.toxicity", "deepeval.metrics.ToxicityMetric",
            "safety", False, (), "Does the output contain toxic content?",
        ),
        DeepEvalMetricSpec(
            "deepeval.non_advice", "deepeval.metrics.NonAdviceMetric",
            "safety", False, ("advice_types",), "Does the output give unauthorized advice (e.g. financial/medical)?",
        ),
        DeepEvalMetricSpec(
            "deepeval.misuse", "deepeval.metrics.MisuseMetric",
            "safety", False, ("domain",), "Is the output used outside its intended domain-specific purpose?",
        ),
        DeepEvalMetricSpec(
            "deepeval.pii_leakage", "deepeval.metrics.PIILeakageMetric",
            "safety", False, (), "Does the output leak personal identifying information?",
        ),
        DeepEvalMetricSpec(
            "deepeval.role_violation", "deepeval.metrics.RoleViolationMetric",
            "safety", False, ("role",), "Does the output break the agent's assigned role/persona?",
        ),
        DeepEvalMetricSpec(
            "deepeval.geval", "deepeval.metrics.GEval",
            "other", False, ("name", "evaluation_steps"),
            "Custom plain-language LLM-as-judge criteria for subjective scoring.",
        ),
    ]
}


def list_deepeval_catalog() -> list[dict[str, Any]]:
    """Pure metadata — never imports `deepeval`, safe to call even
    without the extra installed. Consumed by recommend.py and (later) a
    dashboard "available metrics" endpoint."""
    return [
        {
            "key": spec.key,
            "category": spec.category,
            "reference_based": spec.reference_based,
            "required_config": list(spec.required_config),
            "description": spec.description,
        }
        for spec in DEEPEVAL_CATALOG.values()
    ]


def build_deepeval_evaluator(
    key: str,
    *,
    threshold: float | None = None,
    model: Any = None,
    **metric_kwargs: Any,
) -> DeepEvalEvaluator:
    """Looks up `key` in DEEPEVAL_CATALOG, lazily imports and constructs
    the real DeepEval metric class, and wraps it in the existing
    DeepEvalEvaluator adapter. Raises UnknownMetricError for an
    unrecognized key and MissingMetricConfigError when a metric's
    required_config kwargs are not supplied — both loud, never a silent
    fallback."""
    spec = DEEPEVAL_CATALOG.get(key)
    if spec is None:
        raise UnknownMetricError(key)

    missing = tuple(name for name in spec.required_config if name not in metric_kwargs)
    if missing:
        raise MissingMetricConfigError(key, missing)

    module_path, class_name = spec.metric_class_path.rsplit(".", 1)
    metric_class = getattr(importlib.import_module(module_path), class_name)

    kwargs: dict[str, Any] = dict(metric_kwargs)
    if threshold is not None:
        kwargs["threshold"] = threshold
    if model is not None:
        kwargs["model"] = model

    metric = metric_class(**kwargs)
    return DeepEvalEvaluator(key, metric)
