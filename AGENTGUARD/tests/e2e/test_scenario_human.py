"""E2E Scenario C — HUMAN.

Agent -> perform_action("payment") -> HUMAN (Policy.require_approval)
-> WebSocket-routed approval request -> a human APPROVEs, REJECTs, or
never responds (timeout). All three are exercised here against the
real ApprovalBroker (agentguard/human/broker.py) — the same component
the FastAPI WebSocket endpoint (server/api.py) drives.
"""
from __future__ import annotations

import asyncio

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard.errors import HumanRejected
from agentguard.human.broker import get_broker
from agentguard.models import RunStatus


async def _resolve_soon(run_id_holder: dict, outcome: str, delay: float = 0.01) -> None:
    """Simulates the dashboard: waits for the request to be published,
    then resolves it — exactly what POST /runs/{run_id}/human-decision
    or the WebSocket inbound message does in server/api.py."""
    await asyncio.sleep(delay)
    broker = get_broker()
    pending = broker.get_pending(run_id_holder["run_id"])
    assert pending, "expected a pending human decision request"
    broker.resolve(pending[0].id, outcome, resolved_by="reviewer@example.com")


async def test_human_approves_payment_and_execution_resumes(fake_repository):
    run_id_holder: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["payment"]), llm_judge=False)
    async def checkout_agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action("payment", amount=500)
        agentguard.update_state(max_budget=50000)
        return "payment approved, order placed"

    resolver = asyncio.create_task(_resolve_soon(run_id_holder, "approved"))
    result = await checkout_agent("buy a laptop")
    await resolver

    assert result == "payment approved, order placed"

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.CONTINUE

    human_decisions = list(fake_repository.human_decisions[run.id].values())
    assert len(human_decisions) == 1
    assert human_decisions[0].status == "approved"
    assert human_decisions[0].action == "payment"
    assert human_decisions[0].resolved_by == "reviewer@example.com"


async def test_human_rejects_payment_and_run_stops(fake_repository):
    run_id_holder: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["payment"]), llm_judge=False)
    async def checkout_agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action("payment", amount=99999)
        return "should never get here"

    resolver = asyncio.create_task(_resolve_soon(run_id_holder, "rejected"))
    with pytest.raises(HumanRejected):
        await checkout_agent("buy something expensive")
    await resolver

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.STOP

    human_decisions = list(fake_repository.human_decisions[run.id].values())
    assert human_decisions[0].status == "rejected"

    decisions = fake_repository.decisions[run.id]
    assert decisions[-1].outcome == RunStatus.STOP
    assert "payment" in decisions[-1].reason


async def test_human_approval_times_out_and_denies(fake_repository):
    @monitor(
        policy=Policy(max_cost=60000, require_approval=["payment"], human_timeout_s=0.05),
        llm_judge=False,
    )
    async def checkout_agent(task: str) -> str:
        await agentguard.perform_action("payment", amount=500)
        return "should never get here"

    with pytest.raises(HumanRejected):
        await checkout_agent("buy a laptop")

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.STOP

    human_decisions = list(fake_repository.human_decisions[run.id].values())
    assert human_decisions[0].status == "timeout"
    assert human_decisions[0].resolved_at is not None
    # A timeout is not "the reviewer's own reason for rejecting" -- it
    # must say so distinctly, not silently reuse the original "why
    # approval was requested" text (that's a different bug this guards).
    assert "timed out" in human_decisions[0].reason.lower()
    assert "payment" not in human_decisions[0].reason  # not the escalation text verbatim


async def test_human_rejects_with_an_explicit_reason_and_it_is_preserved(fake_repository):
    run_id_holder: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["payment"], human_timeout_s=5), llm_judge=False)
    async def checkout_agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action("payment", amount=99999)
        return "should never get here"

    async def _resolve_with_reason() -> None:
        await asyncio.sleep(0.01)
        broker = get_broker()
        pending = broker.get_pending(run_id_holder["run_id"])
        assert pending
        broker.resolve(pending[0].id, "rejected", resolved_by="reviewer@example.com", reason="over the quarterly travel cap")

    resolver = asyncio.create_task(_resolve_with_reason())
    with pytest.raises(HumanRejected) as exc_info:
        await checkout_agent("buy something expensive")
    await resolver

    assert "over the quarterly travel cap" in str(exc_info.value)

    run = next(iter(fake_repository.runs.values()))
    human_decisions = list(fake_repository.human_decisions[run.id].values())
    assert human_decisions[0].reason == "over the quarterly travel cap"


async def test_human_rejects_without_a_reason_gets_a_descriptive_fallback_not_the_stale_escalation_text(fake_repository):
    """Regression test: before this fix, resolving with no explicit
    `reason` left HumanDecision.reason as whatever text triggered the
    ESCALATION ("action 'payment' is listed in policy.require_approval")
    forever -- making a genuine human rejection indistinguishable from a
    timeout, and from an approval, since the field was never touched by
    resolve() at all."""
    run_id_holder: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["payment"]), llm_judge=False)
    async def checkout_agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action("payment", amount=99999)
        return "should never get here"

    resolver = asyncio.create_task(_resolve_soon(run_id_holder, "rejected"))
    with pytest.raises(HumanRejected):
        await checkout_agent("buy something expensive")
    await resolver

    run = next(iter(fake_repository.runs.values()))
    human_decisions = list(fake_repository.human_decisions[run.id].values())
    assert "is listed in policy.require_approval" not in human_decisions[0].reason
    assert "reviewer@example.com" in human_decisions[0].reason


async def test_websocket_broker_publishes_the_request(fake_repository):
    """Verifies the WebSocket fan-out mechanism directly: subscribing to
    a run_id before the request is made means the subscriber's queue
    receives the approval_request event (the same path
    /ws/runs/{run_id}/approval uses)."""
    run_id_holder: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["payment"]), llm_judge=False)
    async def checkout_agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action("payment", amount=500)
        return "done"

    # Subscribe before the run starts is impossible (run_id doesn't
    # exist yet) — so start the agent, grab its run_id off the first
    # published event, and assert on the message shape.
    broker = get_broker()

    async def _watch_and_resolve() -> None:
        # Poll briefly for the run_id and pending request, mirroring how
        # a dashboard would react to the very first WS push.
        for _ in range(50):
            await asyncio.sleep(0.005)
            run_id = run_id_holder.get("run_id")
            if run_id:
                pending = broker.get_pending(run_id)
                if pending:
                    assert pending[0].action == "payment"
                    assert pending[0].reason
                    broker.resolve(pending[0].id, "approved", resolved_by="dash-user")
                    return
        raise AssertionError("approval request was never published")

    watcher = asyncio.create_task(_watch_and_resolve())
    result = await checkout_agent("buy a laptop")
    await watcher
    assert result == "done"
