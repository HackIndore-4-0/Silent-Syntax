"""Integration tests for Phase 3: checkpoint/rollback/counterfactual/audit
persistence, wired together exactly as decorator.py and server/api.py do
— no component mocked, run against the real @monitor loop and the real
FastAPI app (via starlette's TestClient) over the in-memory repository
(no live Postgres in this environment — see docs/EXECUTION_REPORT_PHASE_3.md §1).
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.audit.chain import verify_audit_chain
from agentguard.human.broker import reset_broker
from agentguard.recovery import generate_counterfactual, rollback, seed_recovery_state

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
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


@monitor(policy=Policy(max_cost=60000, version=5), llm_judge=False)
async def _drifting_agent(task: str) -> str:
    agentguard.update_state(max_budget=60000)
    agentguard.reset_state()
    agentguard.update_state(max_budget=67000)
    return "selected a 67000 laptop"


@monitor(policy=Policy(max_cost=60000, version=5), llm_judge=False)
async def _safe_agent(task: str) -> str:
    agentguard.update_state(max_budget=52000)
    return "selected a 52000 laptop"


# -- run -> checkpoint persistence --------------------------------------


async def test_run_to_checkpoint_persistence(fake_repository):
    await _drifting_agent("find a laptop")
    run = next(iter(fake_repository.runs.values()))
    checkpoints = await fake_repository.list_checkpoints(run.id)
    assert [c["label"] for c in checkpoints] == ["S1", "S2", "S3", "S4"]
    assert checkpoints[1]["state"] == {"max_budget": 60000}
    assert all(len(c["state_hash"]) == 64 for c in checkpoints)


# -- run -> rollback persistence + run -> counterfactual persistence -----


async def test_run_to_rollback_and_counterfactual_persistence(fake_repository):
    await _drifting_agent("find a laptop")
    run = next(iter(fake_repository.runs.values()))
    root_cause = await fake_repository.get_root_cause(run.id)
    assert root_cause["earliest_deviation"] == "S3"

    result = await rollback(fake_repository, run.id, "S2")
    assert result.restored_state == {"max_budget": 60000}

    updated_run = await fake_repository.get_run(run.id)
    assert updated_run["status"] == "rolled_back"

    seed_recovery_state(result.restored_state, parent_run_id=run.id, checkpoint_id=result.checkpoint.id)
    await _safe_agent("retry safely")

    recovery_run_id = next(rid for rid in fake_repository.runs if rid != run.id)
    recovery_run = await fake_repository.get_run(recovery_run_id)
    assert recovery_run["status"] == "continue"
    assert recovery_run["parent_run_id"] == run.id

    original_checkpoints = await fake_repository.list_checkpoints(run.id)
    recovery_checkpoints = await fake_repository.list_checkpoints(recovery_run_id)
    cf = generate_counterfactual(
        updated_run, original_checkpoints, recovery_run, recovery_checkpoints, result.checkpoint.id, root_cause
    )
    await fake_repository.save_counterfactual(cf)

    stored = await fake_repository.get_counterfactual(run.id)
    assert stored is not None
    assert stored["comparison"]["actual_status"] == "rolled_back"
    assert stored["comparison"]["counterfactual_status"] == "continue"


# -- audit chain persistence + verification + tamper detection ----------


async def test_audit_chain_persistence_and_verification(fake_repository):
    await _drifting_agent("find a laptop")
    run = next(iter(fake_repository.runs.values()))

    events = await fake_repository.list_audit_events(run.id)
    assert events[0]["event_type"] == "RUN_START"
    assert events[-1]["event_type"] == "RUN_COMPLETION"
    assert {"CHECKPOINT", "EVALUATION", "RISK_ASSESSMENT", "ROOT_CAUSE", "DECISION"}.issubset(
        {e["event_type"] for e in events}
    )
    assert all(e["policy_version"] == 5 for e in events)

    verification = await verify_audit_chain(fake_repository, run.id)
    assert verification["intact"] is True


async def test_tampered_audit_detection(fake_repository):
    await _drifting_agent("find a laptop")
    run = next(iter(fake_repository.runs.values()))

    stored_events = fake_repository.audit_events[run.id]
    tampered_id = stored_events[3].id
    stored_events[3].payload["injected"] = "tampered"

    verification = await verify_audit_chain(fake_repository, run.id)
    assert verification["intact"] is False
    assert verification["first_invalid_event"] == tampered_id


# -- API: timeline, reliability-report, checkpoints, rollback, counterfactual, audit --


def test_timeline_api(client, fake_repository):
    asyncio.run(_drifting_agent("find a laptop"))
    run = next(iter(fake_repository.runs.values()))

    response = client.get(f"/api/runs/{run.id}/timeline")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run.id
    types = [e["event_type"] for e in body["events"]]
    assert types[0] == "RUN_START"
    assert types[-1] == "RUN_COMPLETION"
    assert response_404(client, "timeline")


def test_reliability_report_api(client, fake_repository):
    asyncio.run(_drifting_agent("find a laptop"))
    run = next(iter(fake_repository.runs.values()))

    response = client.get(f"/api/runs/{run.id}/reliability-report")
    assert response.status_code == 200
    body = response.json()
    assert set(body["dimensions"]) == {
        "correctness",
        "goal_completion",
        "constraint_adherence",
        "decision_consistency",
        "tool_usage",
        "behavioral_reliability",
    }
    assert body["dimensions"]["constraint_adherence"]["value"] == 0.0
    assert body["status"] == "stop"


def test_checkpoint_api(client, fake_repository):
    asyncio.run(_drifting_agent("find a laptop"))
    run = next(iter(fake_repository.runs.values()))

    response = client.get(f"/api/runs/{run.id}/checkpoints")
    assert response.status_code == 200
    labels = [c["label"] for c in response.json()]
    assert labels == ["S1", "S2", "S3", "S4"]


def test_rollback_api(client, fake_repository):
    asyncio.run(_drifting_agent("find a laptop"))
    run = next(iter(fake_repository.runs.values()))

    response = client.post(f"/api/runs/{run.id}/rollback", json={"to_checkpoint": "S2"})
    assert response.status_code == 200
    body = response.json()
    assert body["restored_state"] == {"max_budget": 60000}
    assert body["decision"]["outcome"] == "rolled_back"

    bad = client.post(f"/api/runs/{run.id}/rollback", json={"to_checkpoint": "does-not-exist"})
    assert bad.status_code == 400

    missing_run = client.post("/api/runs/does-not-exist/rollback", json={"to_checkpoint": "S2"})
    assert missing_run.status_code == 404


def test_counterfactual_api(client, fake_repository):
    asyncio.run(_drifting_agent("find a laptop"))
    run = next(iter(fake_repository.runs.values()))

    empty = client.get(f"/api/runs/{run.id}/counterfactual")
    assert empty.status_code == 200
    assert empty.json()["counterfactual"] is None

    root_cause = asyncio.run(fake_repository.get_root_cause(run.id))
    rollback_result = asyncio.run(rollback(fake_repository, run.id, "S2"))
    seed_recovery_state(
        rollback_result.restored_state, parent_run_id=run.id, checkpoint_id=rollback_result.checkpoint.id
    )
    asyncio.run(_safe_agent("retry"))
    recovery_run_id = next(rid for rid in fake_repository.runs if rid != run.id)
    recovery_run = asyncio.run(fake_repository.get_run(recovery_run_id))
    updated_run = asyncio.run(fake_repository.get_run(run.id))
    original_checkpoints = asyncio.run(fake_repository.list_checkpoints(run.id))
    recovery_checkpoints = asyncio.run(fake_repository.list_checkpoints(recovery_run_id))
    cf = generate_counterfactual(
        updated_run, original_checkpoints, recovery_run, recovery_checkpoints, rollback_result.checkpoint.id, root_cause
    )
    asyncio.run(fake_repository.save_counterfactual(cf))

    populated = client.get(f"/api/runs/{run.id}/counterfactual")
    assert populated.status_code == 200
    assert populated.json()["result"] == cf.result


def test_audit_api_and_verify(client, fake_repository):
    asyncio.run(_drifting_agent("find a laptop"))
    run = next(iter(fake_repository.runs.values()))

    audit_response = client.get(f"/api/runs/{run.id}/audit")
    assert audit_response.status_code == 200
    body = audit_response.json()
    assert body["event_count"] == len(body["events"])

    verify_response = client.post(f"/api/runs/{run.id}/audit/verify")
    assert verify_response.status_code == 200
    assert verify_response.json()["intact"] is True

    # Tamper directly in the repository, then re-verify via the API.
    fake_repository.audit_events[run.id][2].payload["tampered"] = True
    verify_after_tamper = client.post(f"/api/runs/{run.id}/audit/verify")
    assert verify_after_tamper.json()["intact"] is False


def response_404(client, suffix: str) -> bool:
    return client.get(f"/api/runs/does-not-exist/{suffix}").status_code == 404
