"""End-to-end test of the Phase 1 success condition from the spec:

    @monitor -> Run created -> span captured -> state captured
    -> persisted -> constraint evaluated -> Decision Engine
    -> CONTINUE or STOP -> decision persisted

Uses the in-memory repository fake (tests/fakes.py) so this runs
without a live Postgres instance; tests/integration covers the real
PostgresRunRepository separately.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.models import RunStatus

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def test_agent_within_budget_continues(fake_repository):
    @monitor(policy=Policy(max_cost=60000))
    async def my_agent(task: str) -> str:
        return f"handled: {task}"

    result = await my_agent("find a laptop under budget")

    assert result == "handled: find a laptop under budget"
    assert len(fake_repository.runs) == 1

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.CONTINUE
    assert run.task == "find a laptop under budget"
    assert run.trace_id is not None
    assert run.span_id is not None
    assert run.final_state == {"max_budget": 60000}

    decisions = fake_repository.decisions[run.id]
    assert decisions[-1].outcome == RunStatus.CONTINUE

    evaluations = fake_repository.evaluations[run.id]
    assert evaluations[-1].label == "ok"

    assert len(fake_repository.spans) == 1
    assert fake_repository.spans[0]["run_id"] == run.id


async def test_agent_violating_budget_stops(fake_repository):
    @monitor(policy=Policy(max_cost=60000))
    async def my_agent(task: str) -> str:
        agentguard.update_state(max_budget=67000)
        return "selected a 67000 laptop"

    result = await my_agent("find a laptop under budget")

    assert result == "selected a 67000 laptop"

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.STOP
    assert run.final_state == {"max_budget": 67000}

    evaluations = fake_repository.evaluations[run.id]
    assert evaluations[-1].label == "constraint_violated"
    assert evaluations[-1].evidence["expected"] == 60000
    assert evaluations[-1].evidence["observed"] == 67000

    decisions = fake_repository.decisions[run.id]
    assert decisions[-1].outcome == RunStatus.STOP


async def test_agent_exception_is_recorded_not_swallowed(fake_repository):
    @monitor(policy=Policy(max_cost=60000))
    async def flaky_agent(task: str) -> str:
        raise ValueError("tool call failed")

    with pytest.raises(ValueError, match="tool call failed"):
        await flaky_agent("find a laptop under budget")

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.FAILED
    assert run.exception_type == "ValueError"
    assert run.exception_message == "tool call failed"
    assert run.finished_at is not None
    # A failed run is not evaluated — nothing to score.
    assert fake_repository.evaluations[run.id] == []
    assert fake_repository.decisions[run.id] == []


def test_sync_agent_function_is_supported(fake_repository):
    @monitor(policy=Policy(max_cost=60000))
    def my_sync_agent(task: str) -> str:
        return f"handled sync: {task}"

    result = my_sync_agent("find a laptop under budget")

    assert result == "handled sync: find a laptop under budget"
    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.CONTINUE


async def test_bare_monitor_without_policy_has_no_constraint_to_check(fake_repository):
    @monitor
    async def my_agent(task: str) -> str:
        return "done"

    await my_agent("do something")

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.CONTINUE
    evaluations = fake_repository.evaluations[run.id]
    assert evaluations[-1].label == "not_applicable"


async def test_get_state_outside_monitored_call_raises():
    with pytest.raises(RuntimeError):
        agentguard.get_state()
