"""MetricEvaluator interface.

`EvalCase` is built FROM already-captured Run/TraceStep data (see
`agentguard.evaluation.engine.build_eval_case_from_run`) — never
requires the developer to re-instrument their agent to produce it,
unlike frameworks whose agentic metrics need their own tracing
decorator. It is a plain dataclass, not a pydantic model: it is never
persisted itself (only the `EvaluationResult` a MetricEvaluator
produces from it is), so it doesn't need JSON-safety or the rest of
BaseModel's machinery.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ...models import EvaluationResult


@dataclass
class EvalCase:
    input: Any
    actual_output: Any
    expected_output: Any | None = None
    retrieval_context: list[str] | None = None
    tools_called: list[dict[str, Any]] | None = None
    source_run_id: str | None = None
    source_step_id: str | None = None
    trace_steps: list[dict[str, Any]] | None = None
    """Full ordered TraceStep history for source_run_id, dashboard-shaped
    (list_trace_steps_for_run()'s return shape) — populated by
    build_eval_case_from_run() for evaluators (TrajectoryEvaluator) that
    need the whole step sequence, not just a derived summary."""


class MetricEvaluator(ABC):
    """Base class for anything that scores one EvalCase against one
    quality metric. Async always — a metric may need to make its own
    judge-model call, which must never block synchronously (mirrors
    every other LLM-call path in this codebase)."""

    name: str = "metric_evaluator"

    @abstractmethod
    async def evaluate(self, case: EvalCase) -> EvaluationResult:
        """Score `case`. `evaluation_run_id`/`source_run_id` on the
        returned EvaluationResult are placeholders — the
        EvaluationEngine overwrites them with the real values once the
        case is dispatched, so an implementation may leave them empty."""
        raise NotImplementedError
