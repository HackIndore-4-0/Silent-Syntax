"""Regression test: GET /api/v2/agents/{agent_name}/fingerprint used to
compute its "prior runs" sample count across ALL workspaces globally for
a given agent_name, never scoping by the caller's own workspace_id —
contradicting server/dashboard_v2.py's own stated isolation rule (a run
belonging to another workspace must be indistinguishable from "doesn't
exist"). See FingerprintEngine.fingerprint() / list_runs_by_agent().
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import AgentGuard, Policy
from agentguard._runtime import reset as reset_repository
from agentguard.auth import create_api_key, signup
from agentguard.human.broker import reset_broker
from agentguard.storage.memory import InMemoryRunRepository

AGENT_NAME = "shared_agent_name"


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


def _signup(repo, *, email: str, name: str) -> tuple[str, str]:
    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    return key_result.raw_key, signup_result.workspace.id


def _seed_runs(api_key: str, count: int) -> None:
    guard = AgentGuard(api_key=api_key, project="production")

    @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def shared_agent_name(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    for i in range(count):
        asyncio.run(shared_agent_name(f"task {i}"))


def test_fingerprint_sample_count_is_workspace_isolated(repo, client):
    alice_key, _ = _signup(repo, email="alice-fp@example.com", name="Alice")
    bob_key, _ = _signup(repo, email="bob-fp@example.com", name="Bob")

    _seed_runs(alice_key, 5)  # >= MIN_SAMPLE_SIZE
    _seed_runs(bob_key, 1)    # well under it

    alice_login = client.post("/api/auth/login", json={"email": "alice-fp@example.com", "password": "hunter2222"})
    bob_login = client.post("/api/auth/login", json={"email": "bob-fp@example.com", "password": "hunter2222"})
    alice_client = TestClient(client.app, cookies=alice_login.cookies)
    bob_client = TestClient(client.app, cookies=bob_login.cookies)

    bob_fp = bob_client.get(f"/api/v2/agents/{AGENT_NAME}/fingerprint").json()
    assert bob_fp["sample_count"] == 1, "Bob's fingerprint must not count Alice's runs of the same agent_name"

    alice_fp = alice_client.get(f"/api/v2/agents/{AGENT_NAME}/fingerprint").json()
    assert alice_fp["sample_count"] == 5
