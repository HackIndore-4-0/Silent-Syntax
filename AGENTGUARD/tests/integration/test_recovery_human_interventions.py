"""Regression test for GET /api/v2/recovery undercounting human
interventions.

perform_action()/request_approval() (the LIVE in-function human-review
path — agentguard/context.py) persists a real HumanDecision row but never
saves a Decision(outcome="human"); only the separate post-hoc
low-confidence+high-impact escalation does that. /api/v2/recovery used to
filter purely on Decision.outcome, so a run reviewed and rejected via the
live path was invisible to the Interventions dashboard page despite a real
human_decision existing. See server/dashboard_v2.py's recovery_v2().
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import AgentGuard, Policy
from agentguard._runtime import reset as reset_repository
from agentguard.auth import create_api_key, signup
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


def test_rejected_live_approval_counts_as_a_human_intervention(repo, client):
    signup_result = asyncio.run(signup(repo, email="reviewer@example.com", password="hunter2222", name="Reviewer"))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    guard = AgentGuard(api_key=key_result.raw_key, project="Production")

    run_id_holder: dict = {}
    result_box: dict = {}

    @guard.monitor(policy=Policy(max_cost=60000, require_approval=["payment"]), llm_judge=False)
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

    resolve_response = client.post(f"/api/runs/{run_id}/human-decision", json={"outcome": "rejected"})
    assert resolve_response.status_code == 200

    thread.join(timeout=5)
    assert "error" in result_box  # HumanRejected propagated, run recorded STOP

    login_response = client.post("/api/auth/login", json={"email": "reviewer@example.com", "password": "hunter2222"})
    authed = TestClient(client.app, cookies=login_response.cookies)

    recovery = authed.get("/api/v2/recovery").json()
    assert recovery["human_interventions"] == 1
    matching = [a for a in recovery["attempts"] if a["run_id"] == run_id]
    assert len(matching) == 1
    assert "human" in matching[0]["outcomes"]
