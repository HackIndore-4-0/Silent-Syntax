"""Alternative-tool recovery — Phase 4.

Covers: fallback selection, fallback rejection when unregistered,
recovery after primary failure (spec §25 items 18-20).
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard.errors import ReplanRequested
from agentguard.tools.registry import ToolRegistry, configure_registry, reset_registry

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository
    from agentguard.human.broker import reset_broker

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    reset_registry()
    yield repo
    reset_repository()
    reset_broker()
    reset_registry()


async def test_fallback_selection_after_primary_failure(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)

    calls = {"primary": 0, "fallback": 0}

    async def primary(q: str) -> str:
        calls["primary"] += 1
        raise RuntimeError("search_api down")

    async def fallback(q: str) -> str:
        calls["fallback"] += 1
        return f"results for {q}"

    registry.register_tool("search_api", primary)
    registry.register_tool("search_api_backup", fallback)
    registry.register_alternative("search_api", "search_api_backup", reliability_threshold=0.8)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        return await agentguard.context.call_tool("search_api", "laptop", primary_attempts=2)

    result = await agent("find a laptop")
    assert result == "results for laptop"
    assert calls["primary"] == 2  # "fails twice" per spec
    assert calls["fallback"] == 1

    run_id = next(iter(fake_repository.runs))
    run = await fake_repository.get_run(run_id)
    assert run["status"] == "continue"

    tool_calls = await fake_repository.list_tool_calls_for_run(run_id)
    assert [(t["tool"], t["outcome"], t["is_fallback"]) for t in tool_calls] == [
        ("search_api", "failure", False),
        ("search_api", "failure", False),
        ("search_api_backup", "success", True),
    ]

    events = await fake_repository.list_audit_events(run_id)
    substitutions = [e for e in events if e["event_type"] == "TOOL_SUBSTITUTED"]
    assert len(substitutions) == 1
    assert substitutions[0]["payload"]["primary"] == "search_api"
    assert substitutions[0]["payload"]["fallback"] == "search_api_backup"


async def test_fallback_rejected_when_no_alternative_registered(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)

    async def primary(q: str) -> str:
        raise RuntimeError("down")

    registry.register_tool("search_api", primary)
    # No alternative registered at all.

    @monitor(policy=Policy(max_cost=60000, on_tool_exhausted="replan", max_replans=1), llm_judge=False)
    async def agent(task: str) -> str:
        return await agentguard.context.call_tool("search_api", "laptop", primary_attempts=1)

    # @monitor's replan budget (max_replans=1) lets it try once more, then
    # — since the agent fails every attempt — the REPLAN budget itself is
    # exhausted, the run STOPs, and (per the existing Phase 2 contract)
    # the triggering exception propagates to the caller alongside
    # run.status=STOP. Verify the run reflects "no reliable alternative
    # was ever used", never a silent substitution.
    with pytest.raises(ReplanRequested):
        await agent("find a laptop")
    run_id = next(iter(fake_repository.runs))
    run = await fake_repository.get_run(run_id)
    assert run["status"] == "stop"

    events = await fake_repository.list_audit_events(run_id)
    assert not any(e["event_type"] == "TOOL_SUBSTITUTED" for e in events)
    decisions = [d["outcome"] for d in run["decisions"]]
    assert "replan" in decisions


async def test_fallback_rejected_when_alternative_registered_but_not_registered_as_callable(fake_repository):
    """Registering an alternative NAME without also registering its
    callable must never silently substitute — Rule: no arbitrary tool
    substitution."""
    registry = ToolRegistry()
    configure_registry(registry)

    async def primary(q: str) -> str:
        raise RuntimeError("down")

    registry.register_tool("search_api", primary)
    registry.register_alternative("search_api", "search_api_backup", reliability_threshold=0.8)
    # search_api_backup is never registered as a callable.

    @monitor(policy=Policy(max_cost=60000, on_tool_exhausted="replan", max_replans=1), llm_judge=False)
    async def agent(task: str) -> str:
        return await agentguard.context.call_tool("search_api", "laptop", primary_attempts=1)

    with pytest.raises(ReplanRequested):
        await agent("find a laptop")
    run_id = next(iter(fake_repository.runs))
    run = await fake_repository.get_run(run_id)
    events = await fake_repository.list_audit_events(run_id)
    assert not any(e["event_type"] == "TOOL_SUBSTITUTED" for e in events)
    assert run["status"] == "stop"


async def test_recovery_after_primary_failure_reaches_continue(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)

    async def primary(q: str) -> str:
        raise RuntimeError("down")

    async def fallback(q: str) -> str:
        return "ok"

    registry.register_tool("payment_api", primary)
    registry.register_tool("payment_api_backup", fallback)
    registry.register_alternative("payment_api", "payment_api_backup", reliability_threshold=0.8)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        result = await agentguard.context.call_tool("payment_api", "charge", primary_attempts=1)
        agentguard.update_state(max_budget=50000)
        return result

    result = await agent("charge card")
    assert result == "ok"
    run_id = next(iter(fake_repository.runs))
    run = await fake_repository.get_run(run_id)
    assert run["status"] == "continue"


async def test_call_tool_records_duplicate_calls(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)

    async def tool(q: str) -> str:
        return "ok"

    registry.register_tool("search_api", tool)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        await agentguard.context.call_tool("search_api", "same query")
        await agentguard.context.call_tool("search_api", "same query")
        agentguard.update_state(max_budget=50000)
        return "done"

    await agent("task")
    run_id = next(iter(fake_repository.runs))
    calls = await fake_repository.list_tool_calls_for_run(run_id)
    assert [c["duplicate"] for c in calls] == [False, True]
