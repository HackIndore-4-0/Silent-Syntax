"""E2E Scenario B — ROOT CAUSE + STOP.

The canonical Phase 2 scenario: the constraint (max_budget) silently
disappears from state at S3, then the agent (re)sets it to an
over-budget value at S4. The final evaluator flags the violation using
only the final state (S4) — but the Root-Cause Engine, which sees the
whole ordered state history, correctly attributes the failure to S3.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.models import RunStatus


async def test_root_cause_precedes_the_final_violation(fake_repository):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        # S2: unchanged
        agentguard.update_state(max_budget=60000)
        # S3: the constraint silently disappears (e.g. a buggy
        # summarization pass truncates the agent's working state)
        agentguard.reset_state()
        # S4: agent selects a product that violates the (now-missing)
        # constraint
        agentguard.update_state(max_budget=67000)
        return "selected a 67000 laptop"

    result = await drifting_agent("find a laptop under budget")
    assert result == "selected a 67000 laptop"

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.STOP
    assert run.final_state == {"max_budget": 67000}

    # The naive evaluator's evidence is about the FINAL state only.
    evaluations = fake_repository.evaluations[run.id]
    constraint_eval = next(e for e in evaluations if e.evaluator == "constraint_adherence")
    assert constraint_eval.passed is False
    assert constraint_eval.evidence["observed"] == 67000
    assert constraint_eval.evidence["expected"] == 60000

    # The Root-Cause Engine's evidence is about the EARLIEST deviation.
    root_causes = fake_repository.root_causes[run.id]
    assert len(root_causes) == 1
    root_cause = root_causes[0]
    assert root_cause.earliest_deviation == "S3"
    assert root_cause.expected == {"max_budget": 60000}
    assert root_cause.observed == {"max_budget": None}
    assert root_cause.confidence >= 0.9

    # The eventual STOP decision carries risk evidence too.
    decisions = fake_repository.decisions[run.id]
    stop_decision = decisions[-1]
    assert stop_decision.outcome == RunStatus.STOP
    assert stop_decision.risk_score is not None
    assert stop_decision.risk_score > 0.0
    assert stop_decision.confidence is not None

    risk_assessments = fake_repository.risk_assessments[run.id]
    assert len(risk_assessments) == 1
    assert risk_assessments[0].risk_score == stop_decision.risk_score
