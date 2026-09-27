"""Integration tests for Phase 4: replay, compare, tool profiles,
alternative-tool recovery, behavior fingerprinting, auto-improvement,
policy control portal, and the CI gate — driven through the real
FastAPI app (`starlette.testclient.TestClient`) and/or the real
`@monitor` loop, over the in-memory repository (no live Postgres in
this environment — see docs/EXECUTION_REPORT_PHASE_3.md §1).
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.human.broker import reset_broker
from agentguard.tools.registry import ToolRegistry, configure_registry, reset_registry

from ..fakes import InMemoryRunRepository


@pytest.fixture
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    reset_registry()
    yield repo
    reset_repository()
    reset_broker()
    reset_registry()


@pytest.fixture
def client():
    from server.api import app

    with TestClient(app) as c:
        yield c


def _drifting_agent_run(policy_version: int = 1) -> str:
    @monitor(policy=Policy(max_cost=60000, version=policy_version), llm_judge=False)
    async def drifting_agent(task: str) -> str:
        agentguard.update_state(max_budget=60000)
        agentguard.reset_state()
        agentguard.update_state(max_budget=67000)
        return "over"

    asyncio.run(drifting_agent("find a laptop"))


def _good_agent_run(policy_version: int = 1) -> None:
    @monitor(policy=Policy(max_cost=60000, version=policy_version), llm_judge=False)
    async def good_agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    asyncio.run(good_agent("safe task"))


# -- 1. stored run -> replay -------------------------------------------------


def test_stored_run_to_replay(client, fake_repository):
    _drifting_agent_run()
    run_id = next(iter(fake_repository.runs))

    response = client.get(f"/api/runs/{run_id}/replay")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "safe"
    assert body["no_external_side_effects"] is True
    assert body["root_cause"]["earliest_deviation"] == "S3"
    assert len(body["steps"]) > 0

    assert client.get("/api/runs/does-not-exist/replay").status_code == 404


# -- 2. two runs -> compare ---------------------------------------------------


def test_two_runs_to_compare(client, fake_repository):
    _drifting_agent_run()
    _good_agent_run()
    run_a, run_b = list(fake_repository.runs.keys())

    response = client.get(f"/api/runs/compare?run_a={run_a}&run_b={run_b}")
    assert response.status_code == 200
    body = response.json()
    ca = next(m for m in body["metrics"] if m["metric"] == "constraint_adherence")
    assert ca["value_a"] == 0.0
    assert ca["value_b"] == 1.0

    assert client.get("/api/runs/compare?run_a=missing&run_b=also-missing").status_code == 404


# -- 3. tool events -> ToolProfile --------------------------------------------


def test_tool_events_to_tool_profile(client, fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)

    async def flaky(q: str) -> str:
        raise RuntimeError("down")

    registry.register_tool("search_api", flaky)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        try:
            await agentguard.context.call_tool("search_api", "q", primary_attempts=1)
        except Exception:
            pass
        agentguard.update_state(max_budget=50000)
        return "handled"

    for i in range(6):
        asyncio.run(agent(f"task {i}"))

    response = client.get("/api/tools/search_api/profile")
    assert response.status_code == 200
    profile = response.json()
    assert profile["sample_count"] == 6
    assert profile["reliability"] == "UNRELIABLE"
    assert profile["success_rate"] == 0.0

    assert "search_api" in client.get("/api/tools").json()


# -- 4. failed primary -> alternative tool ------------------------------------


def test_failed_primary_to_alternative_tool(client, fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)

    async def flaky(q: str) -> str:
        raise RuntimeError("down")

    async def backup(q: str) -> str:
        return "backup result"

    registry.register_tool("search_api", flaky)
    registry.register_tool("search_api_backup", backup)
    registry.register_alternative("search_api", "search_api_backup", reliability_threshold=0.8)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        result = await agentguard.context.call_tool("search_api", "q", primary_attempts=2)
        agentguard.update_state(max_budget=50000)
        return result

    asyncio.run(agent("find a laptop"))
    run_id = next(iter(fake_repository.runs))
    run = fake_repository.runs[run_id]
    assert run.status.value == "continue"

    # Portal registration API also works and takes effect immediately.
    response = client.post(
        "/api/tools/alternatives", json={"primary": "payment_api", "fallback": "payment_api_backup", "reliability_threshold": 0.9}
    )
    assert response.status_code == 200
    assert any(a["primary"] == "payment_api" for a in client.get("/api/tools/alternatives").json())


# -- 5. behavior history -> fingerprint ---------------------------------------


def test_behavior_history_to_fingerprint(client, fake_repository):
    for i in range(5):
        _good_agent_run()

    response = client.get("/api/agents/good_agent/fingerprint")
    assert response.status_code == 200
    fp = response.json()
    assert fp["sample_count"] >= 4  # excludes whichever run is treated as "current" if passed
    assert fp["baseline"] is not None


# -- 6. candidate improvement -> regression evaluation ------------------------


def test_candidate_improvement_to_regression_evaluation(fake_repository):
    from agentguard.improve.optimizer import DeterministicTestOptimizer
    from agentguard.improve.workflow import propose_improvement

    _drifting_agent_run()
    run_id = next(iter(fake_repository.runs))

    async def improved_agent(task: str) -> str:
        @monitor(policy=Policy(max_cost=60000), llm_judge=False)
        async def _agent(t: str) -> str:
            agentguard.update_state(max_budget=52000)
            return "within budget"

        await _agent(task)
        return next(rid for rid in fake_repository.runs if rid != run_id)

    candidate, evaluation = asyncio.run(
        propose_improvement(
            fake_repository, run_id, corpus_run_ids=[run_id], candidate_agent_fn=improved_agent, optimizer=DeterministicTestOptimizer()
        )
    )
    assert candidate.status == "proposed"
    assert evaluation is not None
    stored = asyncio.run(fake_repository.list_improvement_evaluations(candidate.id))
    assert len(stored) == 1


# -- 7/8/9. policy creation -> persistence -> new version -> old run keeps old version --


def test_policy_creation_versioning_and_historical_isolation(client, fake_repository):
    response = client.post("/api/policies", json={"name": "demo", "policy": {"max_cost": 60000, "retry_limit": 2}})
    assert response.status_code == 200
    assert response.json()["version"] == 1

    # A run executes against version 1.
    v1 = asyncio.run(_get_current_policy(fake_repository, "demo"))
    _run_with_policy(v1)
    old_run_id = next(iter(fake_repository.runs))

    response = client.post("/api/policies/demo/versions", json={"policy": {"max_cost": 60000, "retry_limit": 9}})
    assert response.status_code == 200
    assert response.json()["version"] == 2

    v2 = asyncio.run(_get_current_policy(fake_repository, "demo"))
    assert v2.version == 2
    assert v2.retry_limit == 9
    _run_with_policy(v2)
    new_run_id = next(rid for rid in fake_repository.runs if rid != old_run_id)

    old_run = asyncio.run(fake_repository.get_run(old_run_id))
    new_run = asyncio.run(fake_repository.get_run(new_run_id))
    assert old_run["policy"]["version"] == 1
    assert old_run["policy"]["retry_limit"] == 2
    assert new_run["policy"]["version"] == 2
    assert new_run["policy"]["retry_limit"] == 9

    # Saving version 2 never touched the persisted version-1 record.
    versions = client.get("/api/policies/demo/versions").json()
    assert versions[0]["policy"]["retry_limit"] == 2
    assert versions[1]["policy"]["retry_limit"] == 9


async def _get_current_policy(repo, name):
    from agentguard.policy.registry import PolicyRegistry

    return await PolicyRegistry(repo).get_current(name)


def _run_with_policy(policy: Policy) -> None:
    @monitor(policy=policy, llm_judge=False)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    asyncio.run(agent("task"))


# -- 10. audit verification CLI -> existing hash chain ------------------------


def test_verify_audit_cli_against_existing_hash_chain(fake_repository):
    from typer.testing import CliRunner

    from agentguard.cli import app

    _drifting_agent_run()
    run_id = next(iter(fake_repository.runs))

    runner = CliRunner()
    result = runner.invoke(app, ["verify-audit", run_id])
    assert result.exit_code == 0
    assert "AUDIT INTACT" in result.output

    # Tamper, then re-verify through the same CLI.
    stored = fake_repository.audit_events[run_id]
    stored[1].payload["tampered"] = True
    result2 = runner.invoke(app, ["verify-audit", run_id])
    assert result2.exit_code == 1
    assert "AUDIT INTEGRITY VIOLATION" in result2.output


# -- 11. CI gate -> correct exit codes ----------------------------------------


def test_ci_gate_correct_exit_codes(fake_repository):
    from typer.testing import CliRunner

    from agentguard.cli import app

    _good_agent_run()
    _drifting_agent_run()
    good_id, bad_id = list(fake_repository.runs.keys())

    runner = CliRunner()
    passing = runner.invoke(app, ["ci-gate", "--baseline-runs", good_id, "--candidate-runs", good_id, "--threshold", "5"])
    assert passing.exit_code == 0

    blocking = runner.invoke(app, ["ci-gate", "--baseline-runs", good_id, "--candidate-runs", bad_id, "--threshold", "5"])
    assert blocking.exit_code == 1

    # Also verified via the HTTP endpoint.
    from starlette.testclient import TestClient

    from server.api import app as fastapi_app

    with TestClient(fastapi_app) as client:
        response = client.post(
            "/api/ci-gate", json={"baseline_run_ids": [good_id], "candidate_run_ids": [bad_id], "threshold_pct": 5.0}
        )
        assert response.status_code == 200
        assert response.json()["result"] == "block"
