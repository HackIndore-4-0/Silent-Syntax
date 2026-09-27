"""CustomEvaluator — the escape hatch for a domain-specific metric with
no built-in adapter. Wraps a plain async function; no framework
dependency, no judge-model call required (though `fn` may make one
itself)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from ...models import EvaluationResult
from .base import EvalCase, MetricEvaluator

CustomEvalFn = Callable[[EvalCase], Awaitable[EvaluationResult]]


class CustomEvaluator(MetricEvaluator):
    def __init__(self, name: str, fn: CustomEvalFn) -> None:
        self.name = name
        self._fn = fn

    async def evaluate(self, case: EvalCase) -> EvaluationResult:
        return await self._fn(case)
