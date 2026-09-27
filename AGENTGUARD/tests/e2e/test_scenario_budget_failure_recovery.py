"""E2E — the canonical Phase 3 budget-failure-recovery scenario, exactly
as the Final Solution spec's canonical example and
examples/budget_failure_recovery.py describe:

    execution -> evaluation -> failure detection -> root-cause
    identification -> checkpoint recovery -> rollback ->
    counterfactual safe-path analysis -> re-execution/recovery ->
    successful completion -> auditable history

This test asserts every concrete value the spec's "END-TO-END TEST" and
"TAMPERING TEST" sections require.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.audit.chain import verify_audit_chain
from agentguard.recovery import generate_counterfactual, rollback, seed_recovery_state


async def test_canonical_budget_failure_recovery_end_to_end(fake_repository):
    policy = Policy(max_cost=60000, version=7)

    @monitor(policy=policy, llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)  # S2
        agentguard.reset_state()  # S3 — constraint disappears (root cause)
        agentguard.update_state(max_budget=67000)  # S4
        return "selected a 67000 laptop"

    @monitor(policy=policy, llm_judge=False)
    async def safe_recovery_agent(task: str) -> str:
        agentguard.update_state(max_budget=52000)
        return "selected a 52000 laptop"

    # -- execution -> evaluation -> failure detection --------------------
    await drifting_agent("find a laptop under budget")
    run = next(iter(fake_repository.runs.values()))
    run_id = run.id

    run_data = await fake_repository.get_run(run_id)
    assert run_data["policy"]["version"] == 7
    assert run_data["status"] == "stop"

    constraint_eval = next(e for e in run_data["evaluations"] if e["evaluator"] == "constraint_adherence")
    assert constraint_eval["passed"] is False
    assert constraint_eval["evidence"]["observed"] == 67000
    assert constraint_eval["evidence"]["expected"] == 60000.0

    checkpoints = await fake_repository.list_checkpoints(run_id)
    assert [c["label"] for c in checkpoints] == ["S1", "S2", "S3", "S4"]
    assert checkpoints[0]["state"] == {"max_budget": 60000.0}
    assert checkpoints[1]["state"] == {"max_budget": 60000}
    assert checkpoints[2]["state"] == {}
    assert checkpoints[3]["state"] == {"max_budget": 67000}

    # -- root-cause identification ----------------------------------------
    root_cause = await fake_repository.get_root_cause(run_id)
    assert root_cause["earliest_deviation"] == "S3"

    # -- decision: STOP -----------------------------------------------------
    decisions = run_data["decisions"]
    assert decisions[-1]["outcome"] == "stop"

    # -- checkpoint recovery / rollback -> S2 --------------------------------
    rollback_result = await rollback(fake_repository, run_id, "S2")
    assert rollback_result.checkpoint.label == "S2"
    assert rollback_result.restored_state == {"max_budget": 60000}
    assert rollback_result.decision.outcome.value == "rolled_back"

    rolled_back_run = await fake_repository.get_run(run_id)
    assert rolled_back_run["status"] == "rolled_back"

    # -- counterfactual safe-path analysis ------------------------------------
    seed_recovery_state(
        rollback_result.restored_state, parent_run_id=run_id, checkpoint_id=rollback_result.checkpoint.id
    )
    recovered_result = await safe_recovery_agent("retry the task with the constraint intact")
    assert recovered_result == "selected a 52000 laptop"

    recovery_run_id = next(rid for rid in fake_repository.runs if rid != run_id)
    recovery_run = await fake_repository.get_run(recovery_run_id)
    assert recovery_run["status"] == "continue"
    assert recovery_run["parent_run_id"] == run_id
    assert recovery_run["final_state"]["max_budget"] == 52000

    recovery_checkpoints = await fake_repository.list_checkpoints(recovery_run_id)
    cf = generate_counterfactual(
        rolled_back_run, checkpoints, recovery_run, recovery_checkpoints, rollback_result.checkpoint.id, root_cause
    )
    await fake_repository.save_counterfactual(cf)
    assert cf.comparison["actual_final_state"] == {"max_budget": 67000}
    assert cf.comparison["counterfactual_final_state"] == {"max_budget": 52000}
    assert "52000" in cf.result

    # -- re-execution / recovery -> successful completion -----------------------
    assert recovery_run["status"] == "continue"

    # -- auditable history: the ORIGINAL run's audit chain is intact -------------
    verification = await verify_audit_chain(fake_repository, run_id)
    assert verification["intact"] is True
    assert verification["event_count"] >= 12  # RUN_START..RUN_COMPLETION plus ROLLBACK

    event_types = [e["event_type"] for e in await fake_repository.list_audit_events(run_id)]
    assert event_types[0] == "RUN_START"
    assert "ROLLBACK" in event_types
    assert event_types[-1] == "ROLLBACK"

    # And the recovery run has its own complete, intact chain too.
    recovery_verification = await verify_audit_chain(fake_repository, recovery_run_id)
    assert recovery_verification["intact"] is True


async def test_tampering_test_isolated_from_production_flow(fake_repository):
    """Spec's dedicated TAMPERING TEST: verify -> PASS, tamper one stored
    payload, verify -> FAIL (identifying the first broken event), restore
    it, verify -> PASS again. Run against the test's own isolated
    InMemoryRunRepository instance (fake_repository fixture) — never
    against any shared/production data."""

    @monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        return "done"

    await agent("do something")
    run = next(iter(fake_repository.runs.values()))

    first = await verify_audit_chain(fake_repository, run.id)
    assert first["intact"] is True

    stored_events = fake_repository.audit_events[run.id]
    target_index = 2
    original_payload = dict(stored_events[target_index].payload)
    tampered_event_id = stored_events[target_index].id
    stored_events[target_index].payload["unauthorized"] = "edit"

    second = await verify_audit_chain(fake_repository, run.id)
    assert second["intact"] is False
    assert second["first_invalid_event"] == tampered_event_id

    stored_events[target_index].payload = original_payload

    third = await verify_audit_chain(fake_repository, run.id)
    assert third["intact"] is True
