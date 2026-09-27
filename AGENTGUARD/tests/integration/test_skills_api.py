"""Skills Generator API routes (GET/POST /api/v2/skills/*)."""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard._runtime import reset as reset_repository
from agentguard.auth import signup
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


def _login(client, *, email: str, name: str, repo):
    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    login_response = client.post("/api/auth/login", json={"email": email, "password": "hunter2222"})
    return {"signup": signup_result, "cookies": login_response.cookies}


def test_options_lists_frameworks_and_categories(repo, client):
    session = _login(client, email="skills1@example.com", name="Skills1", repo=repo)
    c = TestClient(client.app, cookies=session["cookies"])

    response = c.get("/api/v2/skills/options")

    assert response.status_code == 200
    body = response.json()
    assert {f["key"] for f in body["frameworks"]} == {"plain_python", "langgraph", "generic"}
    assert {cat["key"] for cat in body["categories"]} == {
        "rag", "safety", "agentic", "hitl", "trajectory", "benchmarking",
    }
    assert {m["key"] for m in body["judge_models"]} == {"gpt-4o-mini", "gpt-4.1", "gpt-4o"}


def test_generate_for_an_owned_project_returns_markdown(repo, client):
    session = _login(client, email="skills2@example.com", name="Skills2", repo=repo)
    c = TestClient(client.app, cookies=session["cookies"])
    project_id = session["signup"].project.id

    response = c.post("/api/v2/skills/generate", json={
        "project_id": project_id,
        "framework": "plain_python",
        "categories": ["rag"],
        "metrics": {"rag": ["deepeval.faithfulness"]},
    })

    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert "deepeval.faithfulness" in markdown
    assert "${AGENTGUARD_API_KEY}" in markdown


def test_generate_with_a_custom_judge_model_uses_it(repo, client):
    session = _login(client, email="skills6@example.com", name="Skills6", repo=repo)
    c = TestClient(client.app, cookies=session["cookies"])
    project_id = session["signup"].project.id

    response = c.post("/api/v2/skills/generate", json={
        "project_id": project_id,
        "framework": "plain_python",
        "categories": ["safety"],
        "metrics": {"safety": ["deepeval.bias"]},
        "judge_model": "gpt-4o",
    })

    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert "gpt-4o" in markdown
    assert 'default_model="gpt-4o"' in markdown


def test_generate_with_unknown_judge_model_is_400(repo, client):
    session = _login(client, email="skills7@example.com", name="Skills7", repo=repo)
    c = TestClient(client.app, cookies=session["cookies"])
    project_id = session["signup"].project.id

    response = c.post("/api/v2/skills/generate", json={
        "project_id": project_id,
        "framework": "plain_python",
        "categories": [],
        "metrics": {},
        "judge_model": "not-a-real-model",
    })

    assert response.status_code == 400


def test_generate_for_another_workspaces_project_is_404(repo, client):
    alice = _login(client, email="skills3a@example.com", name="Skills3a", repo=repo)
    bob = _login(client, email="skills3b@example.com", name="Skills3b", repo=repo)
    bob_client = TestClient(client.app, cookies=bob["cookies"])

    response = bob_client.post("/api/v2/skills/generate", json={
        "project_id": alice["signup"].project.id,
        "framework": "plain_python",
        "categories": [],
        "metrics": {},
    })

    assert response.status_code == 404


def test_generate_with_unknown_framework_is_400(repo, client):
    session = _login(client, email="skills4@example.com", name="Skills4", repo=repo)
    c = TestClient(client.app, cookies=session["cookies"])
    project_id = session["signup"].project.id

    response = c.post("/api/v2/skills/generate", json={
        "project_id": project_id,
        "framework": "does_not_exist",
        "categories": [],
        "metrics": {},
    })

    assert response.status_code == 400


def test_generate_with_unknown_category_is_400(repo, client):
    session = _login(client, email="skills5@example.com", name="Skills5", repo=repo)
    c = TestClient(client.app, cookies=session["cookies"])
    project_id = session["signup"].project.id

    response = c.post("/api/v2/skills/generate", json={
        "project_id": project_id,
        "framework": "plain_python",
        "categories": ["does_not_exist"],
        "metrics": {},
    })

    assert response.status_code == 400
