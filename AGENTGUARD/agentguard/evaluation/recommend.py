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

from ..models import EvaluationSuite, Recommendation, SuiteMetric
from ..storage.repository import RunRepository

_RETRIEVAL_NAME_HINTS = ("retrieve", "search", "query", "lookup")
_SQL_NAME_HINTS = ("sql", "execute_query", "run_query", "query_db")


def _name_matches(name: str | None, hints: tuple[str, ...]) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(hint in lowered for hint in hints)


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
        for run in runs:
            steps = await self._repository.list_trace_steps_for_run(run["id"])
            if any(_name_matches(s.get("name"), _RETRIEVAL_NAME_HINTS) for s in steps):
                retrieval_hits += 1
            if any(_name_matches(s.get("name"), _SQL_NAME_HINTS) for s in steps):
                sql_hits += 1
            if run.get("parent_run_id"):
                handoff_hits += 1

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
