"""Integration test for GET /api/v2/runs/{run_id}/trace-steps.

Exercises the real gap found in the SDK -> dashboard pipeline: `@traceable`
records TraceStep rows (agentguard/tracing/) for every fine-grained call
made inside a `@monitor`-wrapped run (e.g. a RAG pipeline's retrieval step
and LLM call), but until this endpoint existed there was no dashboard
route exposing them — only the coarser audit-event timeline. Driven
through the real FastAPI app via TestClient, same as test_authorization.py.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import AgentGuard, Policy, traceable
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


@traceable
async def fetch_context(query: str) -> str:
    return f"context for {query!r}"


@traceable
async def summarize(text: str) -> str:
    return f"summary of: {text}"


def test_trace_steps_endpoint_returns_nested_calls(repo, client):
    signup_result = asyncio.run(signup(repo, email="rag@example.com", password="hunter2222", name="Rag"))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    guard = AgentGuard(api_key=key_result.raw_key, project="production")

    @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def research_agent(task: str) -> str:
        context = await fetch_context(task)
        return await summarize(context)

    existing_run_ids = set(repo.runs.keys())
    asyncio.run(research_agent("find a laptop under budget"))
    run_id = next(rid for rid in repo.runs if rid not in existing_run_ids)

    login_response = client.post("/api/auth/login", json={"email": "rag@example.com", "password": "hunter2222"})
    authed = TestClient(client.app, cookies=login_response.cookies)

    response = authed.get(f"/api/v2/runs/{run_id}/trace-steps")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id

    steps = body["steps"]
    names = {s["name"] for s in steps}
    assert any("fetch_context" in n for n in names)
    assert any("summarize" in n for n in names)
    assert all(s["run_id"] == run_id for s in steps)
    # Every recorded call succeeded and carries recorded latency.
    assert all(s["outcome"] == "success" for s in steps)
    assert all(isinstance(s["latency_ms"], (int, float)) for s in steps)
