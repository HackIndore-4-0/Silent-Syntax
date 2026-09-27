from __future__ import annotations

import pytest

from agentguard.checkpoint.engine import CheckpointEngine
from agentguard.models import Policy, Run, RunStatus, StateSnapshot
from agentguard.recovery.rollback import RollbackError, rollback

from ..fakes import InMemoryRunRepository


async def _seeded_stopped_run(repo: InMemoryRunRepository) -> Run:
    run = Run(agent_name="drifting_agent", policy=Policy(max_cost=60000, version=1), status=RunStatus.STOP)
    run.final_state = {"max_budget": 67000}
    await repo.create_run(run)

    engine = CheckpointEngine()
    snapshots = [
        StateSnapshot(label="S1", seq=1, data={"max_budget": 60000}),
        StateSnapshot(label="S2", seq=2, data={"max_budget": 60000}),
        StateSnapshot(label="S3", seq=3, data={}),
        StateSnapshot(label="S4", seq=4, data={"max_budget": 67000}),
    ]
    for snap in snapshots:
        await repo.save_checkpoint(engine.create(run.id, snap))

    await repo.update_run(run)
    return run


async def test_rollback_to_safe_checkpoint_restores_state():
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)

    result = await rollback(repo, run.id, "S2")

    assert result.checkpoint.label == "S2"
    assert result.restored_state == {"max_budget": 60000}
    assert result.previous_state == {"max_budget": 67000}
    assert result.decision.outcome == RunStatus.ROLLED_BACK

    updated = await repo.get_run(run.id)
    assert updated["status"] == RunStatus.ROLLED_BACK.value


async def test_rollback_by_checkpoint_id_works_the_same_as_by_label():
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)
    checkpoints = await repo.list_checkpoints(run.id)
    s2_id = next(c["id"] for c in checkpoints if c["label"] == "S2")

    result = await rollback(repo, run.id, s2_id)
    assert result.checkpoint.id == s2_id


async def test_rollback_rejects_unknown_checkpoint():
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)

    with pytest.raises(RollbackError):
        await rollback(repo, run.id, "S99")


async def test_rollback_rejects_checkpoint_from_a_different_run():
    # list_checkpoints() is scoped to `run_id`, so a checkpoint id that
    # only exists under a different run is indistinguishable from an
    # unknown one — both correctly raise, never silently rolling back to
    # someone else's state.
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)
    other_run = await _seeded_stopped_run(repo)

    other_checkpoints = await repo.list_checkpoints(other_run.id)
    foreign_id = other_checkpoints[0]["id"]

    with pytest.raises(RollbackError, match="not found"):
        await rollback(repo, run.id, foreign_id)


async def test_rollback_rejects_invalid_checkpoint():
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)
    checkpoints = repo.checkpoints[run.id]
    target = next(c for c in checkpoints if c.label == "S2")
    target.valid = False

    with pytest.raises(RollbackError, match="invalid"):
        await rollback(repo, run.id, "S2")


async def test_rollback_rejects_tampered_checkpoint_state():
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)
    checkpoints = repo.checkpoints[run.id]
    target = next(c for c in checkpoints if c.label == "S2")
    target.state["max_budget"] = 999999  # tamper without updating state_hash

    with pytest.raises(RollbackError, match="hash verification"):
        await rollback(repo, run.id, "S2")


async def test_rollback_rejects_a_run_that_is_still_in_flight():
    repo = InMemoryRunRepository()
    run = Run(agent_name="agent", policy=Policy(max_cost=60000), status=RunStatus.RUNNING)
    await repo.create_run(run)

    with pytest.raises(RollbackError, match="has not finished"):
        await rollback(repo, run.id, "S1")


async def test_rollback_records_an_audit_event_with_previous_and_restored_state():
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)

    await rollback(repo, run.id, "S2")

    events = await repo.list_audit_events(run.id)
    rollback_events = [e for e in events if e["event_type"] == "ROLLBACK"]
    assert len(rollback_events) == 1
    payload = rollback_events[0]["payload"]
    assert payload["checkpoint_label"] == "S2"
    assert payload["previous_state"] == {"max_budget": 67000}
    assert payload["restored_state"] == {"max_budget": 60000}
    assert rollback_events[0]["policy_version"] == 1


async def test_rollback_appends_to_an_existing_hash_chain_without_breaking_it():
    from agentguard.audit.chain import build_chain, verify_audit_chain

    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)
    for event in build_chain(run.id, [("RUN_START", {}), ("RUN_COMPLETION", {"status": "stop"})], policy_version=1):
        await repo.save_audit_event(event)

    assert (await verify_audit_chain(repo, run.id))["intact"] is True
    await rollback(repo, run.id, "S2")
    result = await verify_audit_chain(repo, run.id)
    assert result["intact"] is True
    assert result["event_count"] == 3


async def test_rollback_records_a_decision_through_the_decision_engine():
    repo = InMemoryRunRepository()
    run = await _seeded_stopped_run(repo)

    await rollback(repo, run.id, "S2")

    decisions = repo.decisions[run.id]
    assert decisions[-1].outcome == RunStatus.ROLLED_BACK
    assert "S2" in decisions[-1].reason
