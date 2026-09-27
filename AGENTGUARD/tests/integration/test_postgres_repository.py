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
from datetime import datetime, timezone

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


def test_pool_survives_across_separate_asyncio_run_calls():
    """Regression test: a repository instance reused across several
    independent `asyncio.run()` calls — exactly what agentguard's sync
    `@monitor` path (decorator.py's sync_wrapper) and `AgentGuard.__init__`
    (client.py) each do, a brand-new event loop every call — must not
    crash on the second call just because the first call's loop (and
    therefore the asyncpg pool bound to it) is now closed."""
    import asyncio

    repository = PostgresRunRepository(DATABASE_URL)

    async def _create_and_fetch(run_id_holder: list) -> None:
        run = Run(agent_name="loop-affinity-test")
        run_id_holder.append(run.id)
        await repository.create_run(run)
        fetched = await repository.get_run(run.id)
        assert fetched is not None

    run_ids: list[str] = []
    asyncio.run(_create_and_fetch(run_ids))  # first loop; creates the pool
    asyncio.run(_create_and_fetch(run_ids))  # second loop; must recreate the pool, not crash

    async def _cleanup() -> None:
        pool = await repository._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM agentguard_runs WHERE id = ANY($1::text[])", run_ids)
        await repository.close()

    asyncio.run(_cleanup())


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


async def test_list_runs_includes_duration_and_token_fields(repo):
    """Regression test: list_runs()'s hand-written SELECT used to omit
    duration_ms (computed) and model_name/tokens_input/tokens_output/
    estimated_cost_usd (real columns) entirely — present only in
    get_run(). Every dashboard aggregate that reads list_runs() (Latency,
    Tokens & Cost) silently saw None for every run as a result, no matter
    how well-instrumented the agent was."""
    run = Run(agent_name="a", policy=Policy(max_cost=60000))
    await repo.create_run(run)

    run.status = RunStatus.CONTINUE
    run.finished_at = datetime.now(timezone.utc)
    run.model_name = "gpt-4o-mini"
    run.tokens_input = 100
    run.tokens_output = 40
    run.estimated_cost_usd = 0.0012
    await repo.update_run(run)

    [listed] = await repo.list_runs(limit=1)
    assert listed["id"] == run.id
    assert listed["duration_ms"] is not None and listed["duration_ms"] >= 0
    assert listed["model_name"] == "gpt-4o-mini"
    assert listed["tokens_input"] == 100
    assert listed["tokens_output"] == 40
    assert listed["estimated_cost_usd"] == 0.0012


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
