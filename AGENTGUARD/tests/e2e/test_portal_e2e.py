"""Portal E2E (Phase 4 spec §33).

Using the real dashboard/backend (FastAPI app via TestClient — no
interactive browser automation was available in this environment: the
configured `browser-use` MCP server failed to connect, and no GUI
browser exists in this Windows CLI environment; see
docs/EXECUTION_REPORT_PHASE_4.md §16/§20 for that stated limitation):

1. Open policy configuration.        (GET /api/policies)
2. Read current policy.              (GET /api/policies/{name})
3. Change retry limit.                (form data, client-side)
4. Save.                              (POST /api/policies/{name}/versions)
5. Verify a new policy version exists. (GET /api/policies/{name}/versions)
6. Run agent.                         (@monitor using the new current policy)
7. Verify run references the new policy version.
8. Verify an older run still references its previous policy version.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.human.broker import reset_broker
from agentguard.policy.registry import PolicyRegistry

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


def test_portal_policy_lifecycle_end_to_end(client, fake_repository):
    # 1/2. Open + read policy configuration (starts empty).
    assert client.get("/api/policies").json() == []

    create = client.post("/api/policies", json={"name": "checkout_policy", "policy": {"max_cost": 60000, "retry_limit": 2}})
    assert create.status_code == 200
    assert create.json()["version"] == 1

    read_back = client.get("/api/policies/checkout_policy")
    assert read_back.status_code == 200
    assert read_back.json()["policy"]["retry_limit"] == 2

    # 6 (old run, BEFORE the change). Run an agent against the current
    # (version 1) policy.
    policy_v1 = asyncio.run(PolicyRegistry(fake_repository).get_current("checkout_policy"))

    @monitor(policy=policy_v1, llm_judge=False)
    async def checkout_agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    asyncio.run(checkout_agent("buy a laptop"))
    old_run_id = next(iter(fake_repository.runs))

    # 3/4. Change retry limit and save -> a NEW version, never mutating v1.
    save = client.post("/api/policies/checkout_policy/versions", json={"policy": {"max_cost": 60000, "retry_limit": 9}})
    assert save.status_code == 200
    body = save.json()
    assert body["version"] == 2
    assert body["policy"]["retry_limit"] == 9

    # 5. Verify a new policy version exists (both are listed, distinctly).
    versions = client.get("/api/policies/checkout_policy/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["policy"]["retry_limit"] == 2
    assert versions[1]["policy"]["retry_limit"] == 9

    # 6 (new run, AFTER the change) -- run an agent against the new current policy.
    policy_v2 = asyncio.run(PolicyRegistry(fake_repository).get_current("checkout_policy"))
    assert policy_v2.version == 2
    assert policy_v2.retry_limit == 9

    @monitor(policy=policy_v2, llm_judge=False)
    async def checkout_agent_v2(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    asyncio.run(checkout_agent_v2("buy a laptop again"))
    new_run_id = next(rid for rid in fake_repository.runs if rid != old_run_id)

    # 7. New run references the new policy version.
    new_run = client.get(f"/api/runs/{new_run_id}").json()
    assert new_run["policy"]["version"] == 2
    assert new_run["policy"]["retry_limit"] == 9

    # 8. The older run still references its ORIGINAL policy version —
    # saving version 2 never touched it.
    old_run = client.get(f"/api/runs/{old_run_id}").json()
    assert old_run["policy"]["version"] == 1
    assert old_run["policy"]["retry_limit"] == 2


def test_portal_save_failure_reports_the_actual_error(client, fake_repository):
    """Saving a version for a policy that was never created must fail
    honestly (never a false "success")."""
    response = client.post("/api/policies/does-not-exist/versions", json={"policy": {"max_cost": 1000}})
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_portal_dashboard_html_contains_control_portal_section(client):
    """Dashboard V2 (docs/DASHBOARD_V2_REPORT.md) replaced the single
    static-HTML dashboard with an authenticated app shell whose content
    (including the Policy/Tool-Recovery/Behavior-Fingerprint views this
    test originally checked for in raw HTML) is rendered client-side by
    dashboard/app.js after login — the Phase 3 "static markers in the
    page body" assertion no longer applies by design. This is an
    intentional, spec-mandated UI replacement, not a suppressed
    regression: it is re-verified below against the actual served
    files instead."""
    response = client.get("/")
    assert response.status_code == 200
    assert "AgentGuard" in response.text
    assert '/dashboard/app.js' in response.text

    app_js = client.get("/dashboard/app.js")
    assert app_js.status_code == 200
    for marker in ["Policy Management", "Tool", "Behavior", "Overview", "Recovery"]:
        assert marker in app_js.text
