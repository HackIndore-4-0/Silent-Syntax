from __future__ import annotations

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard.replay import replay

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
    @monitor(policy=Policy(max_cost=60000, version=2), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "selected a 67000 laptop"

    await drifting_agent("find a laptop")
    return next(iter(fake_repository.runs))


async def test_replay_object_creation(fake_repository):
    run_id = await _drifting_run(fake_repository)
    session = await replay(fake_repository, run_id)

    assert session.run_id == run_id
    assert session.mode == "safe"
    assert session.no_external_side_effects is True
    assert session.policy_version == 2
    assert session.root_cause is not None
    assert session.root_cause["earliest_deviation"] == "S3"
    assert session.risk is not None
    assert session.confidence == 1.0


async def test_replay_steps_are_ordered_and_classified(fake_repository):
    run_id = await _drifting_run(fake_repository)
    session = await replay(fake_repository, run_id)

    step_numbers = [s.step for s in session.steps]
    assert step_numbers == list(range(1, len(session.steps) + 1))

    kinds = {s.kind for s in session.steps}
    assert kinds == {"event", "state", "evaluation", "decision"}

    state_steps = [s for s in session.steps if s.kind == "state"]
    assert [s.data.get("state") for s in state_steps] == [
        {"max_budget": 60000.0},
        {"max_budget": 60000},
        {},
        {"max_budget": 67000},
    ]

    decision_steps = [s for s in session.steps if s.kind == "decision"]
    assert len(decision_steps) == 1
    assert decision_steps[0].data["outcome"] == "stop"


async def test_replay_is_not_just_a_raw_log_dump(fake_repository):
    """The replay reconstructs CHECKPOINT events with their actual joined
    state (not just a checkpoint_id reference) — evidence this is a real
    reconstruction, not `list_audit_events()` handed back verbatim."""
    run_id = await _drifting_run(fake_repository)
    session = await replay(fake_repository, run_id)

    raw_events = await fake_repository.list_audit_events(run_id)
    checkpoint_events = [e for e in raw_events if e["event_type"] == "CHECKPOINT"]
    assert "state" not in checkpoint_events[0]["payload"]  # raw event has no joined state

    state_steps = [s for s in session.steps if s.kind == "state"]
    assert "state" in state_steps[0].data  # replay's reconstruction DOES have it


async def test_replay_of_unknown_run_raises(fake_repository):
    with pytest.raises(ValueError):
        await replay(fake_repository, "does-not-exist")


async def test_replay_never_executes_agent_code_or_tools(fake_repository, monkeypatch):
    """SAFE/DRY-RUN by construction: replay() never imports/calls the
    original agent function or any registered tool callable — it only
    reads already-persisted repository data."""
    run_id = await _drifting_run(fake_repository)

    called = {"count": 0}

    def _poison(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("replay() must never invoke agent/tool code")

    # If replay() tried to call anything from the tools registry, this
    # would blow up.
    from agentguard.tools.registry import get_registry

    monkeypatch.setattr(get_registry(), "get_callable", _poison)

    session = await replay(fake_repository, run_id)
    assert called["count"] == 0
    assert len(session.steps) > 0
