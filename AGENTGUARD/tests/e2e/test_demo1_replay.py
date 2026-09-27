"""E2E Demo 1 — REPLAY (Phase 4 spec §27).

Uses the canonical Phase 3 budget-failure run: replay it in SAFE mode
and show S1 -> S2 -> S3 (constraint disappears) -> S4 (bad selection)
-> STOP, using the run's own actual stored evidence.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.replay import replay


async def test_demo1_replay_canonical_budget_failure(fake_repository):
    @monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)  # S2
        agentguard.reset_state()  # S3 — constraint disappears
        agentguard.update_state(max_budget=67000)  # S4 — bad selection
        return "selected a 67000 laptop"

    await drifting_agent("find a laptop under budget")
    run_id = next(iter(fake_repository.runs))

    session = await replay(fake_repository, run_id)

    assert session.mode == "safe"
    assert session.no_external_side_effects is True

    state_steps = [s for s in session.steps if s.kind == "state"]
    states = [s.data["state"] for s in state_steps]
    assert states[0] == {"max_budget": 60000.0}  # S1 (seeded from policy.max_cost)
    assert states[1] == {"max_budget": 60000}  # S2
    assert states[2] == {}  # S3 — constraint disappears
    assert states[3] == {"max_budget": 67000}  # S4 — bad selection

    decision_step = next(s for s in session.steps if s.kind == "decision")
    assert decision_step.data["outcome"] == "stop"

    completion_step = session.steps[-1]
    assert completion_step.data["status"] == "stop"

    # The evidence shown is the run's ACTUAL stored root cause / risk —
    # never invented for the replay.
    assert session.root_cause["earliest_deviation"] == "S3"
    real_root_cause = await fake_repository.get_root_cause(run_id)
    assert session.root_cause == real_root_cause
    real_risk = (await fake_repository.list_risk_assessments(run_id))[-1]
    assert session.risk["risk_score"] == real_risk["risk_score"]
