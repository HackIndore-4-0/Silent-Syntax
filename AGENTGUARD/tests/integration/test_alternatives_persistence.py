"""Regression tests for two related bugs found while auditing the Tools
and Models dashboard pages' "Registered Alternatives"/"Registered
Fallbacks" panels:

1. The only prior way to persist a ToolAlternative was the legacy,
   unauthenticated POST /api/tools/alternatives, which never set
   workspace_id — permanently invisible to the workspace-scoped
   GET /api/v2/tools. A new authenticated POST /api/v2/tools/alternatives
   fixes this.
2. repository.save_model_alternative() had zero callers anywhere; the
   actually-used LLM-gateway fallback mechanism (Policy.llm_gateway.
   fallback_chain) was entirely disconnected from the
   agentguard_model_alternatives table GET /api/v2/models reads from.
   decorator.py now auto-persists a run's configured fallback_chain.
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
from agentguard.models import LLMGatewayPolicy, ModelAlternative
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


def _signup_and_login(repo, client, *, email: str, name: str) -> tuple[str, TestClient]:
    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    login = client.post("/api/auth/login", json={"email": email, "password": "hunter2222"})
    return key_result.raw_key, TestClient(client.app, cookies=login.cookies)


def test_v2_tool_alternative_is_workspace_scoped_and_visible(repo, client):
    _, authed = _signup_and_login(repo, client, email="alice-alt@example.com", name="Alice")

    create_response = authed.post(
        "/api/v2/tools/alternatives",
        json={"primary": "vector_search", "fallback": "web_search", "reliability_threshold": 0.75},
    )
    assert create_response.status_code == 200

    tools = authed.get("/api/v2/tools").json()
    assert len(tools["alternatives"]) == 1
    assert tools["alternatives"][0]["primary"] == "vector_search"
    assert tools["alternatives"][0]["fallback"] == "web_search"


def test_configured_fallback_chain_is_auto_persisted_as_model_alternative(repo, client):
    api_key, authed = _signup_and_login(repo, client, email="bob-alt@example.com", name="Bob")
    guard = AgentGuard(api_key=api_key, project="production")

    policy = Policy(
        max_cost=60000,
        llm_gateway=LLMGatewayPolicy(
            fallback_chain=[ModelAlternative(primary_model="gpt-4o", fallback_model="gpt-4o-mini")]
        ),
    )

    @guard.monitor(policy=policy, llm_judge=False)
    async def agent(task: str) -> str:
        return "ok"

    asyncio.run(agent("do something"))

    models = authed.get("/api/v2/models").json()
    assert len(models["alternatives"]) == 1
    assert models["alternatives"][0]["primary_model"] == "gpt-4o"
    assert models["alternatives"][0]["fallback_model"] == "gpt-4o-mini"

    # Re-running must not accumulate duplicate rows (save_model_alternative
    # is an upsert keyed on (workspace_id, primary_model)).
    asyncio.run(agent("do something else"))
    models_again = authed.get("/api/v2/models").json()
    assert len(models_again["alternatives"]) == 1
