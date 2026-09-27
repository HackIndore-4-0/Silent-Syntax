"""E2E Demo 6 — AUTO IMPROVEMENT (Phase 4 spec §32).

Uses the canonical budget-loss failure. Input: failure history, root
cause, recommendation. Generates a candidate improvement, then:
historical replay -> candidate evaluation -> comparison. Candidate
status stays PROPOSED — nothing is auto-deployed. Explicitly identifies
whether a real DSPy optimizer ran or the deterministic stand-in was used
(no ANTHROPIC/DSPy model is configured in this environment, so the
deterministic stand-in is expected — see docs/EXECUTION_REPORT_PHASE_4.md §20).
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.improve import approve_candidate
from agentguard.improve.optimizer import get_default_optimizer
from agentguard.improve.workflow import propose_improvement


async def test_demo6_auto_improvement_proposes_but_never_deploys(fake_repository):
    @monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()  # canonical budget-loss failure
        agentguard.update_state(max_budget=67000)
        return "selected a 67000 laptop"

    await drifting_agent("find a laptop under budget")
    failed_run_id = next(iter(fake_repository.runs))
    root_cause = await fake_repository.get_root_cause(failed_run_id)
    assert root_cause["earliest_deviation"] == "S3"

    async def improved_agent(task: str) -> str:
        """The candidate change realized as a real, runnable agent: the
        constraint is never reset away."""

        @monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False)
        async def _agent(t: str) -> str:
            agentguard.update_state(max_budget=60000)
            agentguard.update_state(max_budget=52000)
            return "selected a 52000 laptop"

        await _agent(task)
        return next(rid for rid in fake_repository.runs if rid != failed_run_id)

    optimizer = get_default_optimizer()
    candidate, evaluation = await propose_improvement(
        fake_repository,
        failed_run_id,
        corpus_run_ids=[failed_run_id],
        candidate_agent_fn=improved_agent,
        optimizer=optimizer,
    )

    # -- input: failure history, root cause, recommendation ------------------
    assert candidate.source_run_id == failed_run_id
    assert candidate.root_cause_summary == root_cause["explanation"]
    assert "immutable" in candidate.recommendation

    # -- explicitly identify whether a REAL DSPy optimizer ran ---------------
    # No ANTHROPIC/DSPy model is configured in this environment (see
    # docs/EXECUTION_REPORT_PHASE_4.md §20), so this MUST be the
    # deterministic stand-in, and it must say so honestly.
    assert candidate.optimizer == "DeterministicTestOptimizer"
    assert candidate.real_dspy_optimizer is False

    # -- historical replay -> candidate evaluation -> comparison -------------
    assert evaluation is not None
    assert evaluation.baseline_run_ids == [failed_run_id]
    assert len(evaluation.candidate_run_ids) == 1
    ca = next(m for m in evaluation.comparison["metrics"] if m["metric"] == "constraint_adherence")
    assert ca["value_a"] == 0.0  # baseline (failed) run
    assert ca["value_b"] == 1.0  # candidate (fixed) run
    assert ca["delta"] == 1.0

    # -- status stays PROPOSED; nothing is auto-deployed ----------------------
    assert candidate.status == "proposed"
    stored = await fake_repository.get_improvement_candidate(candidate.id)
    assert stored["status"] == "proposed"

    # Approval is a distinct, explicit, human-driven step.
    approved = await approve_candidate(fake_repository, candidate.id, "reviewer@example.com")
    assert approved.status == "approved"
    assert approved.approved_by == "reviewer@example.com"
