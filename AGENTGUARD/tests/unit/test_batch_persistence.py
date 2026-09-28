"""Batched checkpoint/audit-event persistence (agentguard/storage/repository.py,
agentguard/decorator.py).

Covers: RunRepository's default save_checkpoints()/save_audit_events()
loop correctly falls back to the singular save_checkpoint()/
save_audit_event() methods, and @monitor's own execution loop calls the
plural methods exactly once per run instead of looping the singular ones
itself (the regression this file exists to prevent: decorator.py used to
call save_checkpoint()/save_audit_event() once per item in a Python for
loop, one Postgres round trip per row).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard.audit.chain import build_chain
from agentguard.checkpoint.engine import CheckpointEngine
from agentguard.models import StateSnapshot

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


async def test_default_save_checkpoints_loops_over_save_checkpoint(fake_repository):
    repo = fake_repository
    singular = AsyncMock(wraps=repo.save_checkpoint)
    repo.save_checkpoint = singular

    engine = CheckpointEngine()
    checkpoints = [
        engine.create("run-1", StateSnapshot(label=f"S{i}", seq=i, data={"i": i}))
        for i in range(3)
    ]

    await repo.save_checkpoints(checkpoints)

    assert singular.await_count == 3
    stored = await repo.list_checkpoints("run-1")
    assert [c["label"] for c in stored] == ["S0", "S1", "S2"]


async def test_default_save_audit_events_loops_over_save_audit_event(fake_repository):
    repo = fake_repository
    singular = AsyncMock(wraps=repo.save_audit_event)
    repo.save_audit_event = singular

    events = build_chain(
        "run-1",
        [("RUN_START", {"a": 1}), ("AGENT_STEP", {"b": 2}), ("RUN_COMPLETION", {"c": 3})],
        policy_version=1,
    )

    await repo.save_audit_events(events)

    assert singular.await_count == 3
    stored = await repo.list_audit_events("run-1")
    assert [e["event_type"] for e in stored] == ["RUN_START", "AGENT_STEP", "RUN_COMPLETION"]


async def test_save_checkpoints_and_save_audit_events_empty_list_is_a_noop(fake_repository):
    repo = fake_repository
    singular_checkpoint = AsyncMock(wraps=repo.save_checkpoint)
    singular_event = AsyncMock(wraps=repo.save_audit_event)
    repo.save_checkpoint = singular_checkpoint
    repo.save_audit_event = singular_event

    await repo.save_checkpoints([])
    await repo.save_audit_events([])

    singular_checkpoint.assert_not_awaited()
    singular_event.assert_not_awaited()


async def test_monitor_calls_batch_methods_exactly_once_per_run(fake_repository):
    """The actual regression guard: decorator.py's finally block must call
    save_checkpoints()/save_audit_events() once each with the full list,
    not loop the singular methods itself. The singular methods still get
    called -- but only from InMemoryRunRepository's own default-loop
    fallback inside save_checkpoints()/save_audit_events(), one level
    down, never directly from decorator.py."""
    repo = fake_repository
    checkpoints_mock = AsyncMock(wraps=repo.save_checkpoints)
    checkpoint_mock = AsyncMock(wraps=repo.save_checkpoint)
    audit_events_mock = AsyncMock(wraps=repo.save_audit_events)
    audit_event_mock = AsyncMock(wraps=repo.save_audit_event)
    repo.save_checkpoints = checkpoints_mock
    repo.save_checkpoint = checkpoint_mock
    repo.save_audit_events = audit_events_mock
    repo.save_audit_event = audit_event_mock

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def my_agent(task: str) -> str:
        agentguard.update_state(seen=task)
        return "ok"

    await my_agent("do something")

    assert checkpoints_mock.await_count == 1
    assert audit_events_mock.await_count == 1

    (checkpoints_arg,), _ = checkpoints_mock.await_args
    (audit_events_arg,), _ = audit_events_mock.await_args
    assert len(audit_events_arg) > 0

    # every singular call traces back to the batch method's own internal
    # fallback loop, not a second, separate loop in decorator.py.
    assert checkpoint_mock.await_count == len(checkpoints_arg)
    assert audit_event_mock.await_count == len(audit_events_arg)
