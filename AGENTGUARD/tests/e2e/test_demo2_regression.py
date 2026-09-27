"""E2E Demo 2 — REGRESSION (Phase 4 spec §28).

Two agent versions (v1, v2) run against the same historical corpus
(identical tasks/policy). Output factual per-dimension metrics and
deltas. No winner is ever labeled.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.regression.compare import compare_runs

TASKS = ["find a laptop under 60000", "find a laptop under 60000 (v2)"]


async def test_demo2_regression_two_agent_versions_against_same_corpus(fake_repository):
    policy = Policy(max_cost=60000, version=1)

    @monitor(policy=policy, llm_judge=False, agent_version="v1")
    async def agent_v1(task: str) -> str:
        # v1: loses the constraint and overspends — the "before" behavior.
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "v1 selected a 67000 laptop"

    @monitor(policy=policy, llm_judge=False, agent_version="v2")
    async def agent_v2(task: str) -> str:
        # v2: the fix — constraint preserved, stays within budget.
        agentguard.update_state(max_budget=60000)
        agentguard.update_state(max_budget=52000)
        return "v2 selected a 52000 laptop"

    await agent_v1(TASKS[0])
    v1_run_id = next(iter(fake_repository.runs))
    await agent_v2(TASKS[0])
    v2_run_id = next(rid for rid in fake_repository.runs if rid != v1_run_id)

    v1_run = await fake_repository.get_run(v1_run_id)
    v2_run = await fake_repository.get_run(v2_run_id)
    assert v1_run["agent_version"] == "v1"
    assert v2_run["agent_version"] == "v2"

    result = await compare_runs(fake_repository, v1_run_id, v2_run_id, label_a="v1", label_b="v2")

    ca = result.get("constraint_adherence")
    assert ca.value_a == 0.0  # v1 violated it
    assert ca.value_b == 1.0  # v2 did not
    assert ca.delta == 1.0

    correctness = result.get("correctness")
    assert correctness.value_a == 0.0
    assert correctness.value_b == 1.0

    # Factual output only — no subjective "winner"/"better" field exists
    # anywhere on the result.
    output = result.to_dict()
    assert "winner" not in output
    for metric in output["metrics"]:
        assert "winner" not in metric and "better" not in metric

    # Both a "higher is better" metric (constraint_adherence, up) and a
    # "lower is better" one (risk, down) are shown side-by-side, exactly
    # as measured — the comparison reports the factual delta either way,
    # never collapsing them into a single verdict.
    risk = result.get("risk")
    assert risk.value_a == 0.75 and risk.value_b == 0.0
    assert risk.delta < 0
    assert ca.delta > 0
