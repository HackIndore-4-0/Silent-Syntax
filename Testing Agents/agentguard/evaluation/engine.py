"""Evaluation orchestrator — Phase 1 scope.

Ties together EvalCase construction, MetricEvaluator dispatch, and
EvaluationResult persistence, proving the trace-native pipeline end to
end with whatever MetricEvaluators are registered on a suite. No
external judge-model adapter, no bounded concurrency/batching, no
result caching, no cost-ceiling enforcement yet — those are later-phase
additions (see the Eval Platform design doc §17); Phase 1's own
verification is a CustomEvaluator run against real Run/TraceStep data,
one case at a time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import EvaluationResult, EvaluationRun, EvaluationSuite
from ..storage.repository import RunRepository
from .evaluators.base import EvalCase, MetricEvaluator

_RETRIEVAL_NAME_HINTS = ("retrieve", "search", "query", "lookup")


def build_eval_case_from_run(
    run: dict[str, Any], trace_steps: list[dict[str, Any]] | None = None
) -> EvalCase:
    """Builds an EvalCase from an already-persisted Run (get_run()'s
    dashboard-shaped dict) plus its TraceStep history
    (list_trace_steps_for_run()) — no new instrumentation, no
    re-running the agent. `retrieval_context` is populated from any
    trace step whose name suggests a retrieval-shaped tool/LLM call;
    this is a heuristic, not a guarantee — a suite that needs a
    reliable retrieval context should populate `EvalCase` directly
    instead of relying on this builder.
    """
    steps = trace_steps or []
    retrieval_context = [
        str(step["output"])
        for step in steps
        if step.get("output") is not None
        and any(hint in (step.get("name") or "").lower() for hint in _RETRIEVAL_NAME_HINTS)
    ]
    tools_called = [
        {"name": step.get("name"), "input": step.get("input"), "output": step.get("output")}
        for step in steps
        if step.get("kind") == "function"
    ]
    llm_call_steps = [s for s in steps if s.get("kind") == "llm_call"]
    # Best-effort anchor for agentguard.evaluation.diagnose: the LAST
    # LLM call in the trace is usually the one that actually produced
    # the run's final answer. For a multi-LLM-call agent this is a
    # heuristic, not a guarantee — a suite that needs a specific step
    # scored should build its own EvalCase with source_step_id set
    # explicitly instead of relying on this builder.
    source_step_id = llm_call_steps[-1]["id"] if llm_call_steps else None
    return EvalCase(
        input=run.get("task"),
        actual_output=run.get("final_state"),
        retrieval_context=retrieval_context or None,
        tools_called=tools_called or None,
        source_run_id=run.get("id"),
        source_step_id=source_step_id,
        trace_steps=steps or None,
    )


class EvaluationEngine:
    """Runs an EvaluationSuite's metrics against a list of EvalCases and
    persists an EvaluationRun + one EvaluationResult per (metric, case).

    `evaluators` maps a SuiteMetric.evaluator key (e.g. "custom.my_metric")
    to a MetricEvaluator instance. A metric with no matching entry
    produces an `available=False` result rather than raising — the same
    "explicit gap, never a silent skip" rule ToolProfile/ModelProfile
    already use for a metric that genuinely couldn't be computed.

    `suite` must already be persisted (`repository.save_evaluation_suite()`)
    before calling `run_suite()` — PostgresRunRepository enforces this
    with a real foreign key (agentguard_evaluation_runs.suite_id ->
    agentguard_evaluation_suites.id); InMemoryRunRepository does not
    enforce it, so a test using only the in-memory repository can miss
    a missing save_evaluation_suite() call that would fail against real
    Postgres.
    """

    def __init__(self, repository: RunRepository, evaluators: dict[str, MetricEvaluator]) -> None:
        self._repository = repository
        self._evaluators = evaluators

    async def run_suite(
        self,
        suite: EvaluationSuite,
        cases: list[EvalCase],
        *,
        workspace_id: str | None = None,
    ) -> EvaluationRun:
        evaluation_run = EvaluationRun(
            suite_id=suite.id,
            workspace_id=workspace_id,
            source_run_ids=[c.source_run_id for c in cases if c.source_run_id],
        )
        await self._repository.save_evaluation_run(evaluation_run)

        for metric in suite.metrics:
            evaluator = self._evaluators.get(metric.evaluator)
            for case in cases:
                if evaluator is None:
                    result = EvaluationResult(
                        evaluation_run_id=evaluation_run.id,
                        source_run_id=case.source_run_id,
                        source_step_id=case.source_step_id,
                        metric=metric.evaluator,
                        available=False,
                        reason=f"no evaluator registered for '{metric.evaluator}'",
                    )
                else:
                    raw = await evaluator.evaluate(case)
                    updates: dict[str, Any] = {
                        "evaluation_run_id": evaluation_run.id,
                        "source_run_id": case.source_run_id,
                        "source_step_id": case.source_step_id,
                        "metric": metric.evaluator,
                    }
                    if metric.threshold is not None and raw.score is not None:
                        updates["passed"] = raw.score >= metric.threshold
                    result = raw.model_copy(update=updates)
                await self._repository.save_evaluation_result(result)

        evaluation_run.status = "complete"
        evaluation_run.finished_at = datetime.now(timezone.utc)
        await self._repository.save_evaluation_run(evaluation_run)
        return evaluation_run
