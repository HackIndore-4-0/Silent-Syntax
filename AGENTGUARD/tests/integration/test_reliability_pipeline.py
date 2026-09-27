"""Integration tests for the Phase 2 reliability pipeline:

    policy -> evaluator -> risk assessment -> decision -> persisted DB record
    root cause -> persisted DB record

These run the real @monitor loop end-to-end against the in-memory fake
repository (no live Postgres required — see tests/integration/
test_postgres_repository.py for the real-Postgres-only suite, which is
skipped without AGENTGUARD_TEST_DATABASE_URL). What's "integration"
here is that no component is mocked: PolicyEngine, ReliabilityEngine
(RiskEngine + RootCauseEngine), and DecisionEngine are the real Phase 2
implementations, wired together exactly as decorator.py wires them.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.human.broker import reset_broker
from agentguard.models import RunStatus

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


async def test_policy_violation_flows_into_decision_and_is_persisted(fake_repository):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=67000)
        return "over budget"

    await agent("buy a laptop")
    run = next(iter(fake_repository.runs.values()))

    # policy -> evaluator: the deterministic evaluator saw the violation.
    evaluations = fake_repository.evaluations[run.id]
    assert any(e.evaluator == "constraint_adherence" and not e.passed for e in evaluations)

    # evaluator -> risk assessment: persisted and reflects the violation.
    risk_assessments = fake_repository.risk_assessments[run.id]
    assert len(risk_assessments) == 1
    assert risk_assessments[0].factors["policy_violation"] == 1.0
    assert risk_assessments[0].risk_score > 0.0

    # risk assessment -> decision: the persisted Decision carries the
    # SAME risk_score the RiskAssessment computed.
    decisions = fake_repository.decisions[run.id]
    assert decisions[-1].outcome == RunStatus.STOP
    assert decisions[-1].risk_score == pytest.approx(risk_assessments[0].risk_score)
    assert len(decisions[-1].policy_findings) == 1
    assert decisions[-1].policy_findings[0].violated is True


async def test_root_cause_is_only_persisted_when_a_deviation_exists(fake_repository):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def well_behaved_agent(task: str) -> str:
        agentguard.update_state(max_budget=55000)
        return "within budget"

    await well_behaved_agent("buy a laptop")
    run = next(iter(fake_repository.runs.values()))
    assert fake_repository.root_causes[run.id] == []  # no violation -> nothing persisted

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()  # constraint disappears
        agentguard.update_state(max_budget=67000)
        return "over budget"

    await drifting_agent("buy a laptop")
    drifting_run = [r for r in fake_repository.runs.values() if r.agent_name == "drifting_agent"][0]
    root_causes = fake_repository.root_causes[drifting_run.id]
    assert len(root_causes) == 1
    assert root_causes[0].run_id == drifting_run.id


async def test_decision_evidence_is_never_a_bare_ai_opinion(fake_repository):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=67000)
        return "over budget"

    await agent("buy a laptop")
    run = next(iter(fake_repository.runs.values()))
    decision = fake_repository.decisions[run.id][-1]

    assert decision.risk_score is not None
    assert decision.confidence is not None
    assert decision.evidence.get("policy_findings")
    assert decision.evidence["policy_findings"][0]["expected"] == 60000
    assert decision.evidence["policy_findings"][0]["observed"] == 67000
