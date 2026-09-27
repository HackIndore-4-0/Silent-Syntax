"""RagasEvaluator — wraps one already-constructed Ragas single-turn
metric (Faithfulness, ContextPrecision, ContextRecall,
ResponseRelevancy, FactualCorrectness, ...) as a MetricEvaluator,
mirroring DeepEvalEvaluator's own "thin wrapper, caller configures the
judge model" design — see that module's docstring for the full
reasoning against re-implementing DeepEval/Ragas' own tracing.

Scoped to Ragas' single-turn metrics (`single_turn_ascore(SingleTurnSample)`)
only. Ragas' multi-turn/agentic metrics (ToolCallAccuracy,
AgentGoalAccuracy) take a `MultiTurnSample` built from a real LangChain
message sequence (HumanMessage/AIMessage/ToolMessage) — AgentGuard's
flat `EvalCase.tools_called` list cannot reconstruct that sequence
faithfully, so wrapping those metrics is left to a future phase rather
than silently half-implemented here.

Disclosure: this adapter follows Ragas' documented public API
(`single_turn_ascore`, `SingleTurnSample`) but was not exercised
against a live `ragas` import in this repo's dev environment — the
installed `ragas==0.4.3` conflicts with the `langchain-community`/
`langchain-core` versions already required by `litellm`/`deepeval` in
this venv (a real, observed pip dependency-resolution conflict, not a
hypothetical). Smoke-test against a real `ragas` install (in its own
virtualenv, if this conflict persists) before relying on this in
production.
"""
from __future__ import annotations

from typing import Any

from ...models import EvaluationResult
from .base import EvalCase, MetricEvaluator


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


class RagasEvaluator(MetricEvaluator):
    def __init__(self, name: str, metric: Any) -> None:
        self.name = name
        self._metric = metric

    async def evaluate(self, case: EvalCase) -> EvaluationResult:
        from ragas.dataset_schema import SingleTurnSample  # deferred: only required if this evaluator is actually used

        sample = SingleTurnSample(
            user_input=_stringify(case.input),
            response=_stringify(case.actual_output),
            reference=_stringify(case.expected_output) if case.expected_output is not None else None,
            retrieved_contexts=case.retrieval_context,
        )

        try:
            score = await self._metric.single_turn_ascore(sample)
        except Exception as exc:
            return EvaluationResult(
                evaluation_run_id="",
                source_run_id=case.source_run_id,
                metric=self.name,
                available=False,
                reason=f"ragas metric '{type(self._metric).__name__}' failed: {exc}",
            )

        return EvaluationResult(
            evaluation_run_id="",
            source_run_id=case.source_run_id,
            metric=self.name,
            score=float(score),
            available=True,
            reason="",
        )
