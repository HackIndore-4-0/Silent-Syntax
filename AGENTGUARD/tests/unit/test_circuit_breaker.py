"""Execution circuit breaker (Policy.circuit_breaker, agentguard/context.py).

Covers: default (None) is a no-op regression guard, a cross-tool
consecutive-failure ceiling trips CircuitBreakerTripped with a
structured halt trace (rule/detail/tool/real code location), a success
in between resets the streak, and a loop-iteration ceiling blocks a
call before the tool itself is ever invoked. Also covers
PolicyEngine.validate rejecting non-positive thresholds.
"""
from __future__ import annotations

import os

import pytest

import agentguard
from agentguard import CircuitBreakerPolicy, Policy, monitor
from agentguard.errors import CircuitBreakerTripped
from agentguard.policy.engine import PolicyEngine, PolicyValidationError
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


class TestNoBreakerConfigured:
    async def test_default_policy_never_enforces_a_threshold(self, fake_repository):
        registry = ToolRegistry()
        configure_registry(registry)

        async def always_fails(q: str) -> str:
            raise RuntimeError("down")

        registry.register_tool("flaky", always_fails)
        seen_counts: list[int] = []

        @monitor(policy=Policy(max_cost=60000), llm_judge=False)
        async def agent(task: str) -> str:
            for _ in range(5):
                try:
                    await agentguard.context.call_tool("flaky", "x", primary_attempts=1)
                except Exception:  # noqa: BLE001 - the agent's own recovery, swallows ReplanRequested too
                    pass
                seen_counts.append(agentguard.context.current_run().consecutive_tool_failures)
            return "done"

        result = await agent("task")
        assert result == "done"
        # The counter is still tracked for telemetry even with no breaker
        # configured — only ENFORCEMENT is opt-in.
        assert seen_counts == [1, 2, 3, 4, 5]

        run_id = next(iter(fake_repository.runs))
        run = await fake_repository.get_run(run_id)
        assert run["status"] == "continue"


class TestConsecutiveFailureBreaker:
    async def test_trips_after_threshold_across_different_tools(self, fake_repository):
        registry = ToolRegistry()
        configure_registry(registry)

        async def tool_a(q: str) -> str:
            raise RuntimeError("a down")

        async def tool_b(q: str) -> str:
            raise RuntimeError("b down")

        registry.register_tool("tool_a", tool_a)
        registry.register_tool("tool_b", tool_b)

        policy = Policy(
            max_cost=60000,
            circuit_breaker=CircuitBreakerPolicy(max_consecutive_tool_failures=4),
        )

        @monitor(policy=policy, llm_judge=False)
        async def agent(task: str) -> str:
            for tool_name in ("tool_a", "tool_a", "tool_b", "tool_b"):
                try:
                    await agentguard.context.call_tool(tool_name, "x", primary_attempts=1)
                except CircuitBreakerTripped:
                    raise
                except Exception:  # noqa: BLE001
                    pass
            return "unreachable"

        with pytest.raises(CircuitBreakerTripped) as exc_info:
            await agent("task")

        exc = exc_info.value
        assert exc.rule == "max_consecutive_tool_failures"
        assert exc.tool == "tool_b"  # the 4th call, whichever tool it was
        # Real code location: the test's own `agent` function, not
        # anything inside agentguard/context.py.
        assert exc.code_function == "agent"
        assert os.path.abspath(__file__) == os.path.abspath(exc.code_file)

        run_id = next(iter(fake_repository.runs))
        run = await fake_repository.get_run(run_id)
        assert run["status"] == "stop"

        decisions = run["decisions"]
        stop_decisions = [d for d in decisions if d["outcome"] == "stop"]
        assert len(stop_decisions) == 1
        evidence = stop_decisions[0]["evidence"]
        assert evidence["rule"] == "max_consecutive_tool_failures"
        assert evidence["tool"] == "tool_b"
        assert evidence["code_function"] == "agent"
        assert evidence["consecutive_tool_failures"] == 4

    async def test_a_success_resets_the_streak(self, fake_repository):
        registry = ToolRegistry()
        configure_registry(registry)

        calls = {"n": 0}

        async def sometimes_fails(q: str) -> str:
            calls["n"] += 1
            # Fails on calls 1-3, succeeds on 4, fails again on 5-7.
            if calls["n"] == 4:
                return "ok"
            raise RuntimeError(f"down on call {calls['n']}")

        registry.register_tool("flaky", sometimes_fails)

        policy = Policy(
            max_cost=60000,
            circuit_breaker=CircuitBreakerPolicy(max_consecutive_tool_failures=4),
        )

        @monitor(policy=policy, llm_judge=False)
        async def agent(task: str) -> str:
            for _ in range(7):
                try:
                    await agentguard.context.call_tool("flaky", "x", primary_attempts=1)
                except Exception:  # noqa: BLE001
                    pass
            agentguard.update_state(max_budget=50000)
            return str(agentguard.context.current_run().consecutive_tool_failures)

        # Neither streak (3, then 3) ever reaches 4 — never trips.
        result = await agent("task")
        assert result == "3"

        run_id = next(iter(fake_repository.runs))
        run = await fake_repository.get_run(run_id)
        assert run["status"] == "continue"


class TestLoopIterationBreaker:
    async def test_blocks_before_the_tool_itself_is_invoked(self, fake_repository):
        registry = ToolRegistry()
        configure_registry(registry)

        calls = {"n": 0}

        async def tool(q: str) -> str:
            calls["n"] += 1
            return "ok"

        registry.register_tool("search", tool)

        policy = Policy(
            max_cost=60000,
            circuit_breaker=CircuitBreakerPolicy(max_loop_iterations=2),
        )

        @monitor(policy=policy, llm_judge=False)
        async def agent(task: str) -> str:
            for _ in range(3):
                await agentguard.context.call_tool("search", "x")
            return "unreachable"

        with pytest.raises(CircuitBreakerTripped) as exc_info:
            await agent("task")

        exc = exc_info.value
        assert exc.rule == "max_loop_iterations"
        assert exc.code_function == "agent"
        # The 3rd call_tool() was blocked before the tool function ran.
        assert calls["n"] == 2

        run_id = next(iter(fake_repository.runs))
        run = await fake_repository.get_run(run_id)
        assert run["status"] == "stop"


class TestPolicyValidation:
    def test_rejects_non_positive_max_consecutive_tool_failures(self):
        policy = Policy(circuit_breaker=CircuitBreakerPolicy(max_consecutive_tool_failures=0))
        with pytest.raises(PolicyValidationError):
            PolicyEngine().validate(policy)

    def test_rejects_non_positive_max_loop_iterations(self):
        policy = Policy(circuit_breaker=CircuitBreakerPolicy(max_loop_iterations=-1))
        with pytest.raises(PolicyValidationError):
            PolicyEngine().validate(policy)

    def test_accepts_a_well_formed_circuit_breaker_policy(self):
        policy = Policy(
            circuit_breaker=CircuitBreakerPolicy(max_consecutive_tool_failures=4, max_loop_iterations=20)
        )
        PolicyEngine().validate(policy)  # does not raise
