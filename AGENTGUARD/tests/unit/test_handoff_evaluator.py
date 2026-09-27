"""HandoffEvaluator (agentguard/evaluation/evaluators/handoff.py).

Covers: no source_run_id, no parent_run_id (not a handoff), a missing
parent run, and the real lost/altered-key scoring against genuine
Run.parent_run_id-linked runs.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation import EvalCase
from agentguard.evaluation.evaluators.handoff import ChainHandoffEvaluator, HandoffEvaluator
from agentguard.models import Run, RunStatus

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _make_run(repo, *, final_state=None, initial_state=None, parent_run_id=None) -> str:
    run = Run(
        agent_name="agent", status=RunStatus.STOP,
        final_state=final_state, initial_state=initial_state or {}, parent_run_id=parent_run_id,
    )
    await repo.create_run(run)
    return run.id


class TestHandoffEvaluator:
    async def test_no_source_run_id_is_unavailable(self, fake_repository):
        evaluator = HandoffEvaluator(fake_repository)
        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a"))
        assert result.available is False

    async def test_run_with_no_parent_is_unavailable(self, fake_repository):
        run_id = await _make_run(fake_repository)
        evaluator = HandoffEvaluator(fake_repository)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id=run_id))

        assert result.available is False
        assert "not a handoff" in result.reason

    async def test_missing_parent_run_is_unavailable(self, fake_repository):
        child_id = await _make_run(fake_repository, parent_run_id="does-not-exist")
        evaluator = HandoffEvaluator(fake_repository)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id=child_id))

        assert result.available is False
        assert "parent run not found" in result.reason

    async def test_perfect_handoff_scores_1_0(self, fake_repository):
        parent_id = await _make_run(fake_repository, final_state={"budget": 100, "task": "refund"})
        child_id = await _make_run(fake_repository, initial_state={"budget": 100, "task": "refund"}, parent_run_id=parent_id)
        evaluator = HandoffEvaluator(fake_repository)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id=child_id))

        assert result.available is True
        assert result.score == 1.0
        assert result.reason == "handoff state preserved exactly"

    async def test_lost_and_altered_keys_lower_the_score(self, fake_repository):
        parent_id = await _make_run(fake_repository, final_state={"budget": 100, "task": "refund", "user_id": "u1"})
        child_id = await _make_run(fake_repository, initial_state={"budget": 50, "task": "refund"}, parent_run_id=parent_id)
        evaluator = HandoffEvaluator(fake_repository)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id=child_id))

        assert result.score == pytest.approx(1.0 - 0.2 - 0.1)  # 1 lost key (user_id), 1 altered key (budget)
        assert "lost keys: ['user_id']" in result.reason
        assert "altered keys: ['budget']" in result.reason


class TestChainHandoffEvaluator:
    async def test_no_source_run_id_is_unavailable(self, fake_repository):
        evaluator = ChainHandoffEvaluator(fake_repository)
        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a"))
        assert result.available is False

    async def test_a_run_with_no_parent_is_unavailable(self, fake_repository):
        run_id = await _make_run(fake_repository)
        evaluator = ChainHandoffEvaluator(fake_repository)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id=run_id))

        assert result.available is False
        assert "no parent chain" in result.reason

    async def test_three_hop_chain_reports_the_weakest_hop_as_overall_score(self, fake_repository):
        # root -> mid: perfect handoff. mid -> leaf: one lost key.
        root_id = await _make_run(fake_repository, final_state={"task": "refund", "budget": 100})
        mid_id = await _make_run(
            fake_repository, initial_state={"task": "refund", "budget": 100},
            final_state={"task": "refund", "budget": 100, "approved_by": "agent_a"},
            parent_run_id=root_id,
        )
        leaf_id = await _make_run(
            fake_repository, initial_state={"task": "refund", "budget": 100},  # approved_by LOST here
            parent_run_id=mid_id,
        )
        evaluator = ChainHandoffEvaluator(fake_repository)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id=leaf_id))

        assert result.available is True
        assert result.score == pytest.approx(1.0 - 0.2)  # the weaker (mid->leaf) hop: 1 lost key
        assert "2 hop(s)" in result.reason
        assert "hop 1" in result.reason and "hop 2" in result.reason

    async def test_all_perfect_hops_score_1_0(self, fake_repository):
        root_id = await _make_run(fake_repository, final_state={"x": 1})
        mid_id = await _make_run(fake_repository, initial_state={"x": 1}, final_state={"x": 1}, parent_run_id=root_id)
        leaf_id = await _make_run(fake_repository, initial_state={"x": 1}, parent_run_id=mid_id)
        evaluator = ChainHandoffEvaluator(fake_repository)

        result = await evaluator.evaluate(EvalCase(input="q", actual_output="a", source_run_id=leaf_id))

        assert result.score == 1.0
