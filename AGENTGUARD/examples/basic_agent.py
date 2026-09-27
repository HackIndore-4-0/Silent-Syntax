"""Runnable proof of the Phase 1 success condition — a real executable
flow, not a mocked screen. Requires a reachable PostgreSQL instance
(see README.md) because it uses the default repository, not a fake.

    python -m agentguard.cli init-db      # once, to create the schema
    python examples/basic_agent.py

Then open the dashboard (uvicorn server.api:app --reload) to see both
runs below listed, one CONTINUE and one STOP.
"""
from __future__ import annotations

import asyncio

from agentguard import Policy, get_state, monitor, update_state


@monitor(policy=Policy(max_cost=60000))
async def find_laptop_within_budget(task: str) -> str:
    """A well-behaved agent: it respects the constraint it was given."""
    print(f"[agent] task: {task}")
    print(f"[agent] constraint seen via get_state(): {get_state().snapshot()}")
    return "selected a 52000 laptop"


@monitor(policy=Policy(max_cost=60000))
async def find_laptop_over_budget(task: str) -> str:
    """A misbehaving agent: it silently drops the budget constraint and
    selects something over it — the canonical AgentGuard demo scenario.
    """
    print(f"[agent] task: {task}")
    update_state(max_budget=67000)
    return "selected a 67000 laptop"


async def main() -> None:
    result_ok = await find_laptop_within_budget("find a laptop under budget")
    print(f"[result] {result_ok}\n")

    result_violation = await find_laptop_over_budget("find a laptop under budget")
    print(f"[result] {result_violation}")


if __name__ == "__main__":
    asyncio.run(main())
