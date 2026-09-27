"""EvaluationRecommendationEngine (agentguard/evaluation/recommend.py).

Covers: retrieval-shaped traces recommend RAG metrics, SQL-shaped
traces recommend a trajectory check, handoff-shaped traces recommend
the handoff metric, no shape detected falls back to trajectory-only,
and every recommended metric carries a real, falsifiable reason. Also
covers that a Recommendation row is actually persisted.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation.recommend import EvaluationRecommendationEngine
from agentguard.models import Run, RunStatus, TraceStep

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _make_run_with_step(repo, *, agent_name, step_name=None, parent_run_id=None):
    run = Run(agent_name=agent_name, status=RunStatus.STOP, parent_run_id=parent_run_id)
    await repo.create_run(run)
    if step_name:
        await repo.save_trace_step(TraceStep(run_id=run.id, kind="function", name=step_name, outcome="success"))
    return run.id


class TestEvaluationRecommendationEngine:
    async def test_retrieval_shaped_traces_recommend_rag_metrics(self, fake_repository):
        for _ in range(5):
            await _make_run_with_step(fake_repository, agent_name="rag_bot", step_name="search_policies")
        engine = EvaluationRecommendationEngine(fake_repository)

        suite = await engine.recommend("rag_bot")

        assert suite.app_type == "rag"
        evaluators = {m.evaluator for m in suite.metrics}
        assert "deepeval.faithfulness" in evaluators
        assert all(m.recommended_by == "auto" for m in suite.metrics)
        assert all("recent runs" in m.reason for m in suite.metrics)

    async def test_sql_shaped_traces_recommend_trajectory(self, fake_repository):
        for _ in range(5):
            await _make_run_with_step(fake_repository, agent_name="sql_bot", step_name="execute_query")
        engine = EvaluationRecommendationEngine(fake_repository)

        suite = await engine.recommend("sql_bot")

        assert "trajectory" in {m.evaluator for m in suite.metrics}

    async def test_handoff_shaped_traces_recommend_handoff_metric(self, fake_repository):
        for _ in range(5):
            await _make_run_with_step(fake_repository, agent_name="orchestrator", parent_run_id="some-parent")
        engine = EvaluationRecommendationEngine(fake_repository)

        suite = await engine.recommend("orchestrator")

        assert "handoff" in {m.evaluator for m in suite.metrics}

    async def test_no_detected_shape_falls_back_to_trajectory_only(self, fake_repository):
        for _ in range(5):
            await _make_run_with_step(fake_repository, agent_name="plain_bot", step_name="format_output")
        engine = EvaluationRecommendationEngine(fake_repository)

        suite = await engine.recommend("plain_bot")

        assert [m.evaluator for m in suite.metrics] == ["trajectory"]
        assert "no retrieval/SQL/handoff shape detected" in suite.metrics[0].reason

    async def test_recommendation_is_persisted(self, fake_repository):
        await _make_run_with_step(fake_repository, agent_name="bot", step_name="search_x")
        engine = EvaluationRecommendationEngine(fake_repository)

        await engine.recommend("bot")

        stored = await fake_repository.list_recommendations(kind="metric_suite")
        assert len(stored) == 1
        assert stored[0]["subject_id"] == "bot"
