"""Integration test: LLM judge -> asynchronous result persistence.

Demonstrates, with actual timing evidence, that:
    1. the LLM evaluator is invoked asynchronously (as a background task)
    2. it returns a structured evaluation
    3. its result is stored (persisted via the repository)
    4. it does NOT execute inside @monitor's synchronous return path —
       @monitor returns before the judge's result exists
    5. when it disagrees strongly with an already-CONTINUE run, the
       Decision Engine (not the judge) records a fresh, explicit
       override decision
"""
from __future__ import annotations

import asyncio
import time

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard.decorator import wait_for_background_tasks
from agentguard.evaluators.llm_judge import LLMJudge
from agentguard.human.broker import reset_broker
from agentguard.llm.provider import DeterministicTestProvider
from agentguard.models import RunStatus

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


async def test_llm_judge_result_is_not_available_when_monitor_returns(fake_repository):
    judge = LLMJudge(provider=DeterministicTestProvider(simulated_latency_s=0.15))

    @monitor(policy=Policy(max_cost=60000), llm_judge=judge)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=55000)
        return "fine"

    t0 = time.monotonic()
    await agent("buy a laptop")
    sync_elapsed = time.monotonic() - t0

    run = next(iter(fake_repository.runs.values()))
    evaluators_immediately_after_return = [e.evaluator for e in fake_repository.evaluations[run.id]]

    # The synchronous path completed well under the judge's simulated
    # 150ms latency, and the judge's evaluation is NOT yet persisted.
    assert sync_elapsed < 0.15
    assert "llm_judge" not in evaluators_immediately_after_return
    assert "constraint_adherence" in evaluators_immediately_after_return

    await wait_for_background_tasks()

    evaluators_after_wait = [e.evaluator for e in fake_repository.evaluations[run.id]]
    assert "llm_judge" in evaluators_after_wait

    llm_eval = next(e for e in fake_repository.evaluations[run.id] if e.evaluator == "llm_judge")
    assert llm_eval.passed is True
    assert "async_elapsed_s" in llm_eval.evidence
    assert llm_eval.evidence["async_elapsed_s"] >= 0.1


async def test_llm_judge_override_records_a_new_decision_when_it_disagrees(fake_repository):
    class AlwaysUnsafeProvider(DeterministicTestProvider):
        async def complete(self, prompt: str) -> str:
            import json

            return json.dumps(
                {
                    "label": "unsafe",
                    "score": 0.1,
                    "confidence": 0.95,
                    "correctness": "fail",
                    "goal_completion": "fail",
                    "reason": "judge disagrees with the deterministic evaluator",
                }
            )

    judge = LLMJudge(provider=AlwaysUnsafeProvider(simulated_latency_s=0.0))

    @monitor(policy=Policy(max_cost=60000), llm_judge=judge)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=55000)  # deterministic evaluator: PASS -> CONTINUE
        return "looks fine to the deterministic evaluator"

    await agent("buy a laptop")
    run = next(iter(fake_repository.runs.values()))

    # Synchronously: CONTINUE, based on the deterministic evaluator alone.
    assert fake_repository.decisions[run.id][-1].outcome == RunStatus.CONTINUE

    await wait_for_background_tasks()

    # The judge itself never called STOP; the Decision Engine did, in a
    # NEW decision row, once the async result came back disagreeing.
    decisions_after = fake_repository.decisions[run.id]
    assert decisions_after[-1].outcome == RunStatus.STOP
    assert "LLM judge" in decisions_after[-1].reason
    assert len(decisions_after) == 2  # original CONTINUE preserved, not mutated

    final_run = fake_repository.runs[run.id]
    assert final_run.status == RunStatus.STOP
