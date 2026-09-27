"""Integration tests against a real PostgreSQL instance.

Set AGENTGUARD_TEST_DATABASE_URL to run these:

    export AGENTGUARD_TEST_DATABASE_URL=postgresql://agentguard:agentguard@localhost:5432/agentguard
    pytest tests/integration

They (and this whole module) are skipped automatically otherwise —
including when asyncpg itself isn't installed, since it's only a core
dependency, not a dev-test dependency.
"""
from __future__ import annotations

import os

import pytest

asyncpg = pytest.importorskip("asyncpg")

from agentguard.models import Decision, EvalResult, Policy, Run, RunStatus  # noqa: E402
from agentguard.storage.postgres import PostgresRunRepository  # noqa: E402

DATABASE_URL = os.environ.get("AGENTGUARD_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="AGENTGUARD_TEST_DATABASE_URL not set; skipping Postgres integration tests",
)


@pytest.fixture
async def repo():
    repository = PostgresRunRepository(DATABASE_URL)
    await repository.init_schema()
    yield repository
    pool = await repository._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agentguard_runs CASCADE")
    await repository.close()


async def test_create_and_get_run(repo):
    run = Run(agent_name="a", task="find a laptop", policy=Policy(max_cost=60000))
    run.initial_state = {"max_budget": 60000}
    await repo.create_run(run)

    run.final_state = {"max_budget": 60000}
    run.status = RunStatus.CONTINUE
    await repo.update_run(run)

    await repo.save_evaluation(
        run, EvalResult(evaluator="constraint_adherence", passed=True, score=1.0, label="ok")
    )
    await repo.save_decision(run, Decision(outcome=RunStatus.CONTINUE, reason="all evaluators passed"))

    fetched = await repo.get_run(run.id)
    assert fetched is not None
    assert fetched["status"] == "continue"
    assert fetched["initial_state"] == {"max_budget": 60000}
    assert fetched["evaluations"][0]["label"] == "ok"
    assert fetched["decisions"][0]["outcome"] == "continue"
    assert fetched["policy"]["max_cost"] == 60000


async def test_get_run_returns_none_for_unknown_id(repo):
    assert await repo.get_run("does-not-exist") is None


async def test_list_runs_orders_by_started_at_desc(repo):
    first = Run(agent_name="a")
    await repo.create_run(first)
    second = Run(agent_name="a")
    await repo.create_run(second)

    runs = await repo.list_runs()
    assert runs[0]["id"] == second.id
    assert runs[1]["id"] == first.id


async def test_failed_run_records_exception(repo):
    run = Run(agent_name="a", policy=Policy(max_cost=60000))
    await repo.create_run(run)

    run.status = RunStatus.FAILED
    run.exception_type = "ValueError"
    run.exception_message = "tool call failed"
    await repo.update_run(run)

    fetched = await repo.get_run(run.id)
    assert fetched["status"] == "failed"
    assert fetched["exception_type"] == "ValueError"
