"""EvaluationRecommendationEngine — Phase 7 (design doc §10): suggests
a starter metric suite by inspecting an agent's REAL recent traced
shape (retrieval-shaped tool calls, SQL-shaped tool calls, multi-agent
handoffs) rather than asking an LLM "what should I measure" — every
recommendation carries the count of real runs it was computed from, so
it is falsifiable and explainable (never a bare, unverifiable
suggestion — see the design doc's own rejection of the "prompt an LLM
for metric suggestions" approach).
"""
from __future__ import annotations

from typing import Any

from ..models import EvaluationSuite, Recommendation, SuiteMetric
from ..storage.repository import RunRepository

_RETRIEVAL_NAME_HINTS = ("retrieve", "search", "query", "lookup")
_SQL_NAME_HINTS = ("sql", "execute_query", "run_query", "query_db")
_SAFETY_TASK_HINTS = ("medical", "legal", "financial", "diagnosis", "advice")

_SHAPE_HIT_RATIO_THRESHOLD = 0.2


def _name_matches(name: str | None, hints: tuple[str, ...]) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(hint in lowered for hint in hints)


def _is_summarization_shaped(run: dict[str, Any]) -> bool:
    """Output at least 3x longer than the task/instruction — a rough,
    length-based proxy for "this run produced a summary of something
    much larger", not a semantic check."""
    task_len = len(str(run.get("task") or ""))
    output_len = len(str(run.get("final_state") or ""))
    return task_len > 0 and output_len >= task_len * 3


def _is_json_output_shaped(run: dict[str, Any]) -> bool:
    """`Run.final_state` is always a dict-or-None by the model's own type
    (never a raw string), so "produces JSON" can't mean "is a JSON
    string" here — it means the dict has genuine nested structure (a
    dict/list value), not just a flat single-field wrapper like
    {"answer": "some text"}."""
    final_state = run.get("final_state")
    if not isinstance(final_state, dict):
        return False
    return any(isinstance(v, (dict, list)) for v in final_state.values())


def _is_safety_sensitive(run: dict[str, Any], steps: list[dict[str, Any]]) -> bool:
    if _name_matches(run.get("task"), _SAFETY_TASK_HINTS):
        return True
    return any(_name_matches(s.get("name"), _SAFETY_TASK_HINTS) for s in steps)


class EvaluationRecommendationEngine:
    def __init__(self, repository: RunRepository) -> None:
        self._repository = repository

    async def recommend(
        self, agent_name: str, *, workspace_id: str | None = None, sample_size: int = 50
    ) -> EvaluationSuite:
        runs = await self._repository.list_runs_by_agent(agent_name, limit=sample_size, workspace_id=workspace_id)
        n = len(runs)

        retrieval_hits = 0
        sql_hits = 0
        handoff_hits = 0
        summarization_hits = 0
        json_output_hits = 0
        safety_hits = 0
        for run in runs:
            steps = await self._repository.list_trace_steps_for_run(run["id"])
            if any(_name_matches(s.get("name"), _RETRIEVAL_NAME_HINTS) for s in steps):
                retrieval_hits += 1
            if any(_name_matches(s.get("name"), _SQL_NAME_HINTS) for s in steps):
                sql_hits += 1
            if run.get("parent_run_id"):
                handoff_hits += 1
            if _is_summarization_shaped(run):
                summarization_hits += 1
            if _is_json_output_shaped(run):
                json_output_hits += 1
            if _is_safety_sensitive(run, steps):
                safety_hits += 1

        metrics: list[SuiteMetric] = []
        app_type = None
        if n and retrieval_hits / n >= 0.2:
            app_type = "rag"
            reason = f"{retrieval_hits} of {n} recent runs included a retrieval-shaped tool call"
            metrics += [
                SuiteMetric(evaluator="deepeval.faithfulness", threshold=0.8, recommended_by="auto", reason=reason),
                SuiteMetric(evaluator="deepeval.contextual_precision", threshold=0.7, recommended_by="auto", reason=reason),
                SuiteMetric(evaluator="deepeval.contextual_recall", threshold=0.7, recommended_by="auto", reason=reason),
                SuiteMetric(evaluator="deepeval.answer_relevancy", threshold=0.7, recommended_by="auto", reason=reason),
            ]
        if n and sql_hits / n >= 0.2:
            app_type = app_type or "sql_agent"
            reason = f"{sql_hits} of {n} recent runs included a SQL-shaped tool call"
            metrics.append(SuiteMetric(evaluator="trajectory", threshold=0.7, recommended_by="auto", reason=reason))
        if n and handoff_hits / n >= 0.2:
            app_type = app_type or "multi_agent"
            reason = f"{handoff_hits} of {n} recent runs had a parent_run_id (a multi-agent handoff)"
            metrics.append(SuiteMetric(evaluator="handoff", threshold=0.8, recommended_by="auto", reason=reason))
        if n and summarization_hits / n >= _SHAPE_HIT_RATIO_THRESHOLD:
            app_type = app_type or "summarization"
            reason = f"{summarization_hits} of {n} recent runs produced output at least 3x longer than the task input"
            metrics.append(SuiteMetric(evaluator="deepeval.summarization", threshold=0.7, recommended_by="auto", reason=reason))
        if n and json_output_hits / n >= _SHAPE_HIT_RATIO_THRESHOLD:
            app_type = app_type or "json_output_agent"
            # deepeval.json_correctness needs a per-agent expected_schema Pydantic
            # class that cannot be inferred here — not auto-added as a SuiteMetric
            # (it would only ever resolve to available=False); surfaced through
            # the persisted Recommendation.reasoning below instead, as a manual
            # follow-up signal.
        if n and safety_hits / n >= _SHAPE_HIT_RATIO_THRESHOLD:
            app_type = app_type or "safety_sensitive"
            reason = f"{safety_hits} of {n} recent runs matched a safety-sensitive keyword (task or tool name)"
            metrics += [
                SuiteMetric(evaluator="deepeval.bias", threshold=0.7, recommended_by="auto", reason=reason),
                SuiteMetric(evaluator="deepeval.toxicity", threshold=0.7, recommended_by="auto", reason=reason),
                SuiteMetric(
                    evaluator="deepeval.non_advice", recommended_by="auto",
                    reason=f"{reason} — supply config={{'advice_types': [...]}} before this metric will run",
                ),
            ]
        if not metrics:
            metrics.append(
                SuiteMetric(
                    evaluator="trajectory",
                    recommended_by="auto",
                    reason=f"no retrieval/SQL/handoff shape detected across {n} recent runs — trajectory diagnosis is the only shape-independent default",
                )
            )

        suite = EvaluationSuite(name=f"{agent_name}_recommended", workspace_id=workspace_id, app_type=app_type, metrics=metrics)

        evidence_ids = [m.evaluator for m in metrics]
        recommendation = Recommendation(
            workspace_id=workspace_id,
            kind="metric_suite",
            subject_id=agent_name,
            recommendation=suite.model_dump(),
            reasoning=f"analyzed {n} recent runs of '{agent_name}'; app_type={app_type!r}",
            evidence_ids=evidence_ids,
        )
        await self._repository.save_recommendation(recommendation)

        return suite
