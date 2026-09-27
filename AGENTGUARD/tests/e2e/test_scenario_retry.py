"""E2E Scenario A — RETRY.

RUNNING -> EVALUATING -> RETRY -> execution succeeds -> CONTINUE.

A transient tool failure on the first attempt is retried automatically
(bounded by Policy.retry_limit) and the run ultimately succeeds.
"""
from __future__ import annotations

import agentguard
from agentguard import Policy, monitor
from agentguard.errors import TransientError
from agentguard.models import RunStatus


async def test_transient_failure_is_retried_then_succeeds(fake_repository):
    attempts = {"count": 0}

    @monitor(policy=Policy(max_cost=60000, retry_limit=2), llm_judge=False)
    async def flaky_agent(task: str) -> str:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise TransientError("tool timed out", tool="search_catalog")
        agentguard.update_state(max_budget=55000)
        return "selected a 55000 laptop"

    result = await flaky_agent("find a laptop under budget")

    assert result == "selected a 55000 laptop"
    assert attempts["count"] == 2

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.CONTINUE
    assert run.retry_count == 1

    decisions = fake_repository.decisions[run.id]
    outcomes = [d.outcome for d in decisions]
    assert outcomes == [RunStatus.RETRY, RunStatus.CONTINUE]
    assert decisions[0].retry_count == 1
    assert decisions[0].reason.startswith("tool timed out") or "tool timed out" in decisions[0].reason

    # Two attempts -> two spans persisted (retry is auditable in the trace).
    assert len(fake_repository.spans) == 2
    assert fake_repository.spans[0]["attributes"]["agentguard.attempt"] == 1
    assert fake_repository.spans[1]["attributes"]["agentguard.attempt"] == 2


async def test_retry_limit_exceeded_falls_through_to_replan(fake_repository):
    @monitor(policy=Policy(max_cost=60000, retry_limit=1, on_retry_exhausted="replan", max_replans=1), llm_judge=False)
    async def always_flaky_agent(task: str) -> str:
        agentguard.get_replan_context()  # available even before any replan
        raise TransientError("tool permanently down")

    try:
        await always_flaky_agent("find a laptop under budget")
        assert False, "expected the exhausted retry/replan budget to raise"
    except Exception as exc:  # noqa: BLE001
        assert "tool permanently down" in str(exc) or "replan limit" in str(exc)

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.STOP

    decisions = fake_repository.decisions[run.id]
    outcomes = [d.outcome for d in decisions]
    # Every retry budget exhaustion falls through to a replan (its own
    # fresh retry budget), and once that is exhausted too, STOP. Bounded
    # and terminates — never loops forever.
    assert outcomes[-1] == RunStatus.STOP
    assert RunStatus.RETRY in outcomes
    assert RunStatus.REPLAN in outcomes
    assert len(outcomes) < 10


async def test_retry_limit_exceeded_stops_when_configured(fake_repository):
    @monitor(policy=Policy(max_cost=60000, retry_limit=1, on_retry_exhausted="stop"), llm_judge=False)
    async def always_flaky_agent(task: str) -> str:
        raise TransientError("tool permanently down")

    try:
        await always_flaky_agent("find a laptop under budget")
        assert False, "expected retry exhaustion to raise"
    except TransientError:
        pass

    run = next(iter(fake_repository.runs.values()))
    assert run.status == RunStatus.STOP
    decisions = fake_repository.decisions[run.id]
    outcomes = [d.outcome for d in decisions]
    # attempt 1 fails -> RETRY (budget available); attempt 2 fails ->
    # RETRY (budget now exhausted, recorded) -> STOP (on_retry_exhausted="stop")
    assert outcomes == [RunStatus.RETRY, RunStatus.RETRY, RunStatus.STOP]
