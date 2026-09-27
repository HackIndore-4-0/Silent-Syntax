"""DeepEvalEvaluator — wraps ONE already-constructed DeepEval metric
(any `deepeval.metrics.BaseMetric` subclass: FaithfulnessMetric,
AnswerRelevancyMetric, ContextualPrecisionMetric, ContextualRecallMetric,
a custom GEval instance, ...) as a MetricEvaluator.

Deliberately thin: it constructs `deepeval.test_case.LLMTestCase`
directly from AgentGuard's own `EvalCase` and calls the metric's
`a_measure()` — it never touches DeepEval's own tracing (`@observe()`)
or dataset (`EvaluationDataset`/`Golden`) machinery. AgentGuard's
TraceStep/audit trail stays the single source of truth for "what
happened during this run"; DeepEval is used purely as a scoring
library, exactly the role `litellm` plays for model calls (see the
"AgentGuard Eval Platform" design doc §16 for the full reasoning).

The `deepeval` package is only imported lazily, inside `evaluate()` —
`import agentguard` (and `import agentguard.evaluation`) never requires
it. Install with `pip install agentguard[deepeval]`.

A metric failure (a judge-model call that errors, a missing API key,
DeepEval's own internal exception) is caught here and reported as
`available=False` — one metric failing must never crash an entire
EvaluationEngine.run_suite() call over every other case/metric.
"""
from __future__ import annotations

from typing import Any

from ...models import EvaluationResult
from .base import EvalCase, MetricEvaluator


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _to_deepeval_tool_calls(tools_called: list[dict[str, Any]] | None) -> list[Any] | None:
    if not tools_called:
        return None
    from deepeval.test_case import ToolCall  # deferred: only required if this evaluator is actually used

    return [
        ToolCall(name=t.get("name") or "", input_parameters=t.get("input"), output=t.get("output"))
        for t in tools_called
    ]


class DeepEvalEvaluator(MetricEvaluator):
    def __init__(self, name: str, metric: Any) -> None:
        self.name = name
        self._metric = metric

    async def evaluate(self, case: EvalCase) -> EvaluationResult:
        from deepeval.test_case import LLMTestCase  # deferred: only required if this evaluator is actually used

        test_case = LLMTestCase(
            input=_stringify(case.input),
            actual_output=_stringify(case.actual_output),
            expected_output=_stringify(case.expected_output) if case.expected_output is not None else None,
            retrieval_context=case.retrieval_context,
            tools_called=_to_deepeval_tool_calls(case.tools_called),
        )

        try:
            await self._metric.a_measure(test_case, _show_indicator=False)
        except Exception as exc:
            return EvaluationResult(
                evaluation_run_id="",
                source_run_id=case.source_run_id,
                metric=self.name,
                available=False,
                reason=f"deepeval metric '{type(self._metric).__name__}' failed: {exc}",
            )

        return EvaluationResult(
            evaluation_run_id="",
            source_run_id=case.source_run_id,
            metric=self.name,
            score=self._metric.score,
            passed=self._metric.success,
            available=True,
            reason=self._metric.reason or "",
            judge_model=getattr(self._metric, "evaluation_model", None),
            cost_usd=getattr(self._metric, "evaluation_cost", None),
        )
