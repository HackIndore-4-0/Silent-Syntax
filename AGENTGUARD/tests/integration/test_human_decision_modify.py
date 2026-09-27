"""Regression/feature test for the HITL "modify" capability: a reviewer
resolving a pending approval with corrected parameters (e.g. a
counter-price) instead of a bare approve/reject, and the agent reading
those edited values back via perform_action_with_result() to resume with
THEM instead of the original proposal — the piece Challenge 2's rubric
("...approve, modify, or reject...") needed that plain approve/reject
didn't cover.
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.human.broker import reset_broker
from agentguard.storage.memory import InMemoryRunRepository


@pytest.fixture
def repo():
    r = InMemoryRunRepository()
    agentguard.configure(r)
    reset_broker()
    yield r
    reset_repository()
    reset_broker()


@pytest.fixture
def client(repo):
    from server.api import app

    with TestClient(app) as c:
        yield c


def _run_agent_in_background_loop(coro_factory, result_box: dict) -> threading.Thread:
    def _target() -> None:
        try:
            result_box["result"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001
            result_box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread


def test_modified_evidence_resumes_agent_with_edited_values(repo, client):
    run_id_holder: dict = {}
    result_box: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["book_flight"]), llm_judge=False)
    async def booking_agent(task: str) -> dict:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        result = await agentguard.perform_action_with_result("book_flight", price=12000)
        decision = result.human_decision
        price = decision.modified_evidence.get("price") if decision and decision.modified_evidence else 12000
        return {"booked_price": price}

    async def _run() -> dict:
        return await booking_agent("book a flight")

    thread = _run_agent_in_background_loop(_run, result_box)
    for _ in range(200):
        if "run_id" in run_id_holder:
            break
        threading.Event().wait(0.005)
    run_id = run_id_holder["run_id"]

    for _ in range(200):
        threading.Event().wait(0.005)
        response = client.get(f"/api/runs/{run_id}/human-decisions")
        if response.json():
            break

    resolve_response = client.post(
        f"/api/runs/{run_id}/human-decision",
        json={"outcome": "approved", "modified_evidence": {"price": 9500}},
    )
    assert resolve_response.status_code == 200

    thread.join(timeout=5)
    assert "error" not in result_box, result_box.get("error")
    assert result_box["result"] == {"booked_price": 9500}

    decisions = client.get(f"/api/runs/{run_id}/human-decisions").json()
    assert decisions[0]["status"] == "approved"
    assert decisions[0]["modified_evidence"] == {"price": 9500}


def test_rejection_reason_from_the_rest_api_reaches_the_live_waiter(repo, client):
    """Regression test: POST /api/runs/{run_id}/human-decision accepts a
    `reason` field, but for a LIVE in-function wait (an agent coroutine
    currently suspended in perform_action) it used to be silently dropped
    -- ApprovalBroker.resolve() never accepted or applied it, so the
    reviewer's actual explanation never reached the resulting
    HumanRejected exception or the persisted HumanDecision."""
    run_id_holder: dict = {}
    result_box: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["book_flight"]), llm_judge=False)
    async def booking_agent(task: str) -> dict:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action_with_result("book_flight", price=12000)
        return {"booked": True}

    async def _run() -> dict:
        return await booking_agent("book a flight")

    thread = _run_agent_in_background_loop(_run, result_box)
    for _ in range(200):
        if "run_id" in run_id_holder:
            break
        threading.Event().wait(0.005)
    run_id = run_id_holder["run_id"]

    for _ in range(200):
        threading.Event().wait(0.005)
        response = client.get(f"/api/runs/{run_id}/human-decisions")
        if response.json():
            break

    resolve_response = client.post(
        f"/api/runs/{run_id}/human-decision",
        json={"outcome": "rejected", "reason": "vendor is on the sanctions list"},
    )
    assert resolve_response.status_code == 200

    thread.join(timeout=5)
    assert "error" in result_box
    assert "vendor is on the sanctions list" in str(result_box["error"])

    decisions = client.get(f"/api/runs/{run_id}/human-decisions").json()
    assert decisions[0]["status"] == "rejected"
    assert decisions[0]["reason"] == "vendor is on the sanctions list"
