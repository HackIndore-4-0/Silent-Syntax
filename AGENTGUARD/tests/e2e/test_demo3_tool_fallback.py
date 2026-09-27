"""E2E Demo 3 — TOOL FALLBACK (Phase 4 spec §29).

Primary search tool fails; the registered alternative succeeds.
Expected: primary failure -> ToolProfile evaluated -> fallback selected
-> successful result -> CONTINUE. All events persisted.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.tools.registry import ToolRegistry, configure_registry, reset_registry


async def test_demo3_primary_search_fails_alternative_succeeds(fake_repository):
    reset_registry()
    registry = ToolRegistry()
    configure_registry(registry)

    async def primary_search(query: str) -> str:
        raise RuntimeError("search_api is down")

    async def alternative_search(query: str) -> str:
        return f"results for {query!r} from the backup search index"

    registry.register_tool("search_api", primary_search)
    registry.register_tool("search_api_backup", alternative_search)
    registry.register_alternative("search_api", "search_api_backup", reliability_threshold=0.8)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def shopping_agent(task: str) -> str:
        result = await agentguard.context.call_tool("search_api", "laptop under budget", primary_attempts=2)
        agentguard.update_state(max_budget=50000)
        return result

    result = await shopping_agent("find a laptop")

    assert "backup search index" in result

    run_id = next(iter(fake_repository.runs))
    run = await fake_repository.get_run(run_id)
    assert run["status"] == "continue"

    tool_calls = await fake_repository.list_tool_calls_for_run(run_id)
    assert [(c["tool"], c["outcome"]) for c in tool_calls] == [
        ("search_api", "failure"),
        ("search_api", "failure"),
        ("search_api_backup", "success"),
    ]

    events = await fake_repository.list_audit_events(run_id)
    event_types = [e["event_type"] for e in events]
    assert event_types.index("TOOL_CALL") < event_types.index("TOOL_SUBSTITUTED")
    substitution = next(e for e in events if e["event_type"] == "TOOL_SUBSTITUTED")
    assert substitution["payload"]["primary"] == "search_api"
    assert substitution["payload"]["fallback"] == "search_api_backup"

    reset_registry()
