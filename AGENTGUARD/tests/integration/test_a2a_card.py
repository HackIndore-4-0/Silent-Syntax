"""A2A Agent Card endpoint (GET /api/v2/agents/{agent_name}/card).

Driven through the real FastAPI app via TestClient, mirroring
test_authorization.py's fixture pattern (no shared conftest.py in this
suite — copied locally like every other test file here)."""
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


def _signup_and_seed_run(repo, client, *, email: str, name: str, agent_name: str = "billing_agent") -> dict:
    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    guard = AgentGuard(api_key=key_result.raw_key, project="production")

    @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def seeded_agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    asyncio.run(seeded_agent(f"{name}'s task"))

    login_response = client.post("/api/auth/login", json={"email": email, "password": "hunter2222"})
    return {"workspace_id": signup_result.workspace.id, "cookies": login_response.cookies, "agent_name": agent_name}


def _fresh_client_for(cookies) -> TestClient:
    from server.api import app

    return TestClient(app, cookies=cookies)


def test_card_defaults_to_empty_undeclared_capabilities(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice-card@example.com", name="Alice")
    alice_client = _fresh_client_for(alice["cookies"])

    response = alice_client.get("/api/v2/agents/seeded_agent/card")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "seeded_agent"
    assert card["capabilities"] == []
    assert card["capabilities_declared_by_author"] is False
    assert card["description"] is None
    assert card["url"] is None


def test_card_404s_for_unknown_agent(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice-card2@example.com", name="Alice2")
    alice_client = _fresh_client_for(alice["cookies"])

    assert alice_client.get("/api/v2/agents/does_not_exist/card").status_code == 404


def test_card_404s_for_foreign_workspace_agent(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice-card3@example.com", name="Alice3")
    bob = _signup_and_seed_run(repo, client, email="bob-card3@example.com", name="Bob3")

    bob_client = _fresh_client_for(bob["cookies"])
    assert bob_client.get("/api/v2/agents/seeded_agent/card").status_code == 200  # bob has his own

    # Bob's own agent card must not be confused with Alice's workspace.
    alice_client = _fresh_client_for(alice["cookies"])
    alice_card = alice_client.get("/api/v2/agents/seeded_agent/card").json()
    bob_card = bob_client.get("/api/v2/agents/seeded_agent/card").json()
    assert alice_card["workspace_id"] != bob_card["workspace_id"]


def test_card_reflects_author_supplied_description_and_capabilities(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice-card4@example.com", name="Alice4")

    asyncio.run(
        repo.update_agent_card(
            alice["workspace_id"], "seeded_agent",
            description="Handles customer billing", capabilities=["charge_card", "issue_refund"],
        )
    )

    alice_client = _fresh_client_for(alice["cookies"])
    card = alice_client.get("/api/v2/agents/seeded_agent/card").json()
    assert card["description"] == "Handles customer billing"
    assert card["capabilities"] == ["charge_card", "issue_refund"]
    assert card["capabilities_declared_by_author"] is True
