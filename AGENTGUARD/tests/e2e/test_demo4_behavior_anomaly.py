"""E2E Demo 4 — BEHAVIOR ANOMALY (Phase 4 spec §30).

Build sufficient historical runs (normal: 7 tool calls each), then
produce one anomalous run (19 tool calls). Expected: a behavior
deviation is detected and reported with baseline, current, difference,
sample_count, and anomaly state.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.reliability.fingerprint import FingerprintEngine
from agentguard.tools.registry import ToolRegistry, configure_registry, reset_registry


async def test_demo4_behavior_deviation_detected(fake_repository):
    reset_registry()
    registry = ToolRegistry()
    configure_registry(registry)

    async def lookup(i: int) -> str:
        return f"result {i}"

    registry.register_tool("lookup", lookup)

    async def run_agent(n_calls: int) -> None:
        @monitor(policy=Policy(max_cost=60000), llm_judge=False)
        async def research_agent(task: str) -> str:
            for i in range(n_calls):
                await agentguard.context.call_tool("lookup", i)
            agentguard.update_state(max_budget=50000)
            return "done"

        await research_agent("research task")

    # Sufficient history: 6 normal runs (>= MIN_SAMPLE_SIZE=5), 7 tool
    # calls each.
    for _ in range(6):
        await run_agent(7)

    # The anomalous run: 19 tool calls.
    await run_agent(19)
    current_run_id = list(fake_repository.runs.keys())[-1]

    fingerprint = await FingerprintEngine(fake_repository).fingerprint("research_agent", current_run_id=current_run_id)

    assert fingerprint.sample_count == 6  # excludes the current run itself
    assert round(fingerprint.baseline["tool_calls"], 1) == 7.0
    assert fingerprint.current["tool_calls"] == 19.0
    assert fingerprint.deviation["tool_calls"] > fingerprint.anomaly_threshold
    assert fingerprint.anomaly is True

    reset_registry()


async def test_demo4_insufficient_history_never_flags_an_anomaly(fake_repository):
    reset_registry()
    registry = ToolRegistry()
    configure_registry(registry)

    async def lookup(i: int) -> str:
        return f"result {i}"

    registry.register_tool("lookup", lookup)

    async def run_agent(n_calls: int) -> None:
        @monitor(policy=Policy(max_cost=60000), llm_judge=False)
        async def research_agent(task: str) -> str:
            for i in range(n_calls):
                await agentguard.context.call_tool("lookup", i)
            agentguard.update_state(max_budget=50000)
            return "done"

        await research_agent("research task")

    # Only 3 prior runs — below MIN_SAMPLE_SIZE (5).
    for _ in range(3):
        await run_agent(7)
    await run_agent(19)
    current_run_id = list(fake_repository.runs.keys())[-1]

    fingerprint = await FingerprintEngine(fake_repository).fingerprint("research_agent", current_run_id=current_run_id)
    assert fingerprint.sample_count == 3
    assert fingerprint.baseline is None
    assert fingerprint.anomaly is False

    reset_registry()
