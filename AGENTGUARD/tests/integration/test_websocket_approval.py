"""Integration tests for the real WebSocket human-approval channel:

    Agent -> Decision Engine -> HUMAN -> FastAPI WebSocket -> Dashboard
    -> human decision -> WebSocket response -> Decision Engine
    -> RESUME / REPLAN / STOP

Drives the actual `server/api.py` FastAPI app (not a reimplementation)
via starlette's TestClient, which runs the app in a real (if
synchronous-driving) ASGI event loop — this is a genuine WebSocket
handshake and message exchange, not a mock.
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

from ..fakes import InMemoryRunRepository


@pytest.fixture
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


@pytest.fixture
def client():
    from server.api import app

    with TestClient(app) as c:
        yield c


def _run_agent_in_background_loop(coro_factory, result_box: dict) -> threading.Thread:
    """Runs the @monitor-wrapped agent (which awaits a live human
    decision) on its own event loop / thread, so the main thread is free
    to drive the WebSocket TestClient (itself running a background
    loop) — mirrors an agent process and a dashboard being different
    processes in reality.
    """

    def _target() -> None:
        try:
            result_box["result"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001
            result_box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread


def test_websocket_receives_pending_approval_and_can_approve(fake_repository, client):
    run_id_holder: dict = {}
    result_box: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["payment"]), llm_judge=False)
    async def checkout_agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action("payment", amount=500)
        return "payment approved"

    async def _run() -> str:
        return await checkout_agent("buy a laptop")

    thread = _run_agent_in_background_loop(_run, result_box)

    # Wait for the run_id to exist, then open the WebSocket for it.
    for _ in range(200):
        if "run_id" in run_id_holder:
            break
        threading.Event().wait(0.005)
    assert "run_id" in run_id_holder, "agent never published a run_id"
    run_id = run_id_holder["run_id"]

    with client.websocket_connect(f"/ws/runs/{run_id}/approval") as ws:
        message = ws.receive_json()
        assert message["type"] == "approval_request"
        assert message["request"]["action"] == "payment"
        assert message["request"]["run_id"] == run_id

        ws.send_json({"outcome": "approved", "resolved_by": "dash-user"})
        ack = ws.receive_json()
        assert ack["type"] == "human_decision_ack"
        assert ack["resolved"] is True

    thread.join(timeout=5)
    assert "error" not in result_box, result_box.get("error")
    assert result_box["result"] == "payment approved"


def test_rest_endpoint_resolves_pending_human_decision(fake_repository, client):
    run_id_holder: dict = {}
    result_box: dict = {}

    @monitor(policy=Policy(max_cost=60000, require_approval=["payment"]), llm_judge=False)
    async def checkout_agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        await agentguard.perform_action("payment", amount=500)
        return "should not reach here"

    async def _run() -> str:
        return await checkout_agent("buy a laptop")

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

    response = client.post(f"/api/runs/{run_id}/human-decision", json={"outcome": "rejected"})
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True

    thread.join(timeout=5)
    assert "error" in result_box

    run_response = client.get(f"/api/runs/{run_id}")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "stop"


def test_api_endpoints_expose_risk_and_root_cause(fake_repository, client):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "over budget"

    asyncio.run(drifting_agent("buy a laptop"))
    run = next(iter(fake_repository.runs.values()))

    risk_response = client.get(f"/api/runs/{run.id}/risk")
    assert risk_response.status_code == 200
    assert len(risk_response.json()) == 1

    root_cause_response = client.get(f"/api/runs/{run.id}/root-cause")
    assert root_cause_response.status_code == 200
    assert root_cause_response.json()["earliest_deviation"] == "S3"

    decisions_response = client.get(f"/api/runs/{run.id}/decisions")
    assert decisions_response.status_code == 200
    assert decisions_response.json()[-1]["outcome"] == "stop"


def test_get_run_404_for_unknown_id(client, fake_repository):
    assert client.get("/api/runs/does-not-exist").status_code == 404
    assert client.get("/api/runs/does-not-exist/risk").status_code == 404
