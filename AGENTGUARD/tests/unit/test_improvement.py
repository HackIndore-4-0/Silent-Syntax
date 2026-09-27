from __future__ import annotations

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard.improve import ApprovalError, approve_candidate, generate_recommendation, reject_candidate
from agentguard.improve.optimizer import DeterministicTestOptimizer
from agentguard.improve.workflow import propose_improvement

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository
    from agentguard.human.broker import reset_broker

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


async def _drifting_run(fake_repository) -> str:
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "over"

    await drifting_agent("find a laptop")
    return next(iter(fake_repository.runs))


def test_recommendation_generation_matches_the_disappearance_pattern():
    root_cause = {
        "earliest_deviation": "S3",
        "explanation": "max_budget disappeared during state transition S3",
        "expected": {"max_budget": 60000},
        "evidence": {"policy_attr": "max_cost"},
    }
    rec = generate_recommendation(root_cause)
    assert "max_budget" in rec.problem
    assert "immutable" in rec.recommendation
    assert rec.candidate_change["before"] and rec.candidate_change["after"]
    assert rec.expected_benefit


def test_recommendation_generation_handles_no_root_cause():
    rec = generate_recommendation(None)
    assert rec.candidate_change == {}


async def test_candidate_creation_via_deterministic_optimizer(fake_repository):
    run_id = await _drifting_run(fake_repository)
    candidate, evaluation = await propose_improvement(
        fake_repository, run_id, corpus_run_ids=[], optimizer=DeterministicTestOptimizer()
    )
    assert candidate.status == "proposed"
    assert candidate.optimizer == "DeterministicTestOptimizer"
    assert candidate.real_dspy_optimizer is False
    assert evaluation is None  # no candidate_agent_fn supplied -> no fabricated evaluation

    stored = await fake_repository.get_improvement_candidate(candidate.id)
    assert stored is not None
    assert stored["status"] == "proposed"


async def test_candidate_evaluation_replays_corpus_against_a_real_candidate_agent(fake_repository):
    run_id = await _drifting_run(fake_repository)

    async def improved_agent(task: str) -> str:
        @monitor(policy=Policy(max_cost=60000), llm_judge=False)
        async def _agent(t: str) -> str:
            agentguard.update_state(max_budget=52000)
            return "within budget"

        await _agent(task)
        return next(rid for rid in fake_repository.runs if rid != run_id)

    candidate, evaluation = await propose_improvement(
        fake_repository, run_id, corpus_run_ids=[run_id], candidate_agent_fn=improved_agent,
        optimizer=DeterministicTestOptimizer(),
    )
    assert evaluation is not None
    assert evaluation.baseline_run_ids == [run_id]
    assert len(evaluation.candidate_run_ids) == 1
    ca_metric = next(m for m in evaluation.comparison["metrics"] if m["metric"] == "constraint_adherence")
    assert ca_metric["value_a"] == 0.0  # baseline (the drifting run) violated the constraint
    assert ca_metric["value_b"] == 1.0  # candidate (improved agent) did not
    assert ca_metric["delta"] == 1.0

    stored_evals = await fake_repository.list_improvement_evaluations(candidate.id)
    assert len(stored_evals) == 1


async def test_approval_requires_explicit_approver(fake_repository):
    run_id = await _drifting_run(fake_repository)
    candidate, _ = await propose_improvement(fake_repository, run_id, corpus_run_ids=[], optimizer=DeterministicTestOptimizer())

    with pytest.raises(ApprovalError):
        await approve_candidate(fake_repository, candidate.id, "")

    approved = await approve_candidate(fake_repository, candidate.id, "dev@example.com")
    assert approved.status == "approved"
    assert approved.approved_by == "dev@example.com"
    assert approved.resolved_at is not None


async def test_cannot_approve_a_candidate_twice(fake_repository):
    run_id = await _drifting_run(fake_repository)
    candidate, _ = await propose_improvement(fake_repository, run_id, corpus_run_ids=[], optimizer=DeterministicTestOptimizer())
    await approve_candidate(fake_repository, candidate.id, "dev@example.com")

    with pytest.raises(ApprovalError):
        await approve_candidate(fake_repository, candidate.id, "someone-else@example.com")


async def test_reject_candidate(fake_repository):
    run_id = await _drifting_run(fake_repository)
    candidate, _ = await propose_improvement(fake_repository, run_id, corpus_run_ids=[], optimizer=DeterministicTestOptimizer())
    rejected = await reject_candidate(fake_repository, candidate.id, "dev@example.com")
    assert rejected.status == "rejected"


async def test_never_auto_deploys():
    """Structural guarantee: ImprovementCandidate has no field or method
    that applies itself anywhere — approval only ever changes `status`."""
    from agentguard.models import ImprovementCandidate

    assert not hasattr(ImprovementCandidate, "deploy")
    assert not hasattr(ImprovementCandidate, "apply")
