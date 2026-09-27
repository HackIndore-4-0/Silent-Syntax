"""Runnable proof of the Phase 2 scenarios — a real executable flow,
not a mocked screen. Requires a reachable PostgreSQL instance (see
README.md), same as examples/basic_agent.py.

    python -m agentguard.cli init-db      # once, to create/upgrade the schema
    python examples/phase2_agent.py

Then open the dashboard (uvicorn server.api:app --reload) to see all
three runs: one RETRY-then-CONTINUE, one ROOT-CAUSE STOP, and one
pending HUMAN approval (best explored live in the dashboard, since it
needs a real APPROVE/REJECT click over the WebSocket channel — see
tests/e2e/test_scenario_human.py for the fully automated version of
that same flow).
"""
from __future__ import annotations

import asyncio

from agentguard import Policy, get_replan_context, monitor, perform_action, reset_state, update_state
from agentguard.errors import TransientError


@monitor(policy=Policy(max_cost=60000, retry_limit=2))
async def flaky_price_lookup_agent(task: str) -> str:
    """Scenario A — RETRY: a transient tool failure is retried
    automatically (bounded by policy.retry_limit) and the run succeeds."""
    state = getattr(flaky_price_lookup_agent, "_calls", 0)
    flaky_price_lookup_agent._calls = state + 1
    if state == 0:
        raise TransientError("price lookup service timed out", tool="price_api")
    update_state(max_budget=52000)
    return "selected a 52000 laptop"


@monitor(policy=Policy(max_cost=60000))
async def drifting_agent(task: str) -> str:
    """Scenario B — ROOT CAUSE + STOP: the budget constraint silently
    disappears from state (S3) before the agent picks an over-budget
    item (S4). The Root-Cause Engine correctly attributes the failure
    to S3, not to S4/S5 where the deterministic evaluator notices it."""
    update_state(max_budget=60000)
    reset_state()  # the constraint is lost here — this is the real root cause
    update_state(max_budget=67000)
    return "selected a 67000 laptop"


@monitor(policy=Policy(max_cost=60000, require_approval=["payment"], human_timeout_s=120))
async def checkout_agent(task: str) -> str:
    """Scenario C — HUMAN: a payment action requires live approval.
    Running this standalone will leave the run parked in HUMAN status
    (waiting on the dashboard) until human_timeout_s elapses and it is
    auto-denied — approve/reject it from the dashboard within 120s to
    see RESUME or STOP instead."""
    await perform_action("payment", amount=1200)
    return "payment approved, order placed"


async def main() -> None:
    print("=== Scenario A: RETRY ===")
    result = await flaky_price_lookup_agent("find a laptop under budget")
    print(f"[result] {result}\n")

    print("=== Scenario B: ROOT CAUSE + STOP ===")
    result = await drifting_agent("find a laptop under budget")
    print(f"[result] {result}\n")

    print("=== Scenario C: HUMAN (open the dashboard within 120s to approve/reject) ===")
    try:
        result = await checkout_agent("buy a laptop")
        print(f"[result] {result}")
    except Exception as exc:  # noqa: BLE001 - demo script: show whatever the reviewer decided
        print(f"[result] run ended without a normal return: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
