"""evaluation_suite_run job handler (agentguard/jobs/handlers.py).

Covers: the happy path (suite + real Run/TraceStep data -> persisted
EvaluationResults via the existing EvaluationEngine), a missing source
run being skipped rather than failing the whole job, a bad suite_id
failing immediately via PermanentJobFailure (no wasted retries), and a
suite metric with no matching evaluator producing an available=False
result without failing the job -- all exactly matching
EvaluationEngine.run_suite()'s existing "never let one bad thing abort
the whole run" contract.
"""
from __future__ import annotations

import agentguard
import pytest
from agentguard.evaluation import EvaluationSuite, SuiteMetric
from agentguard.jobs import EVALUATION_SUITE_RUN_JOB_KIND, PermanentJobFailure, Worker, make_evaluation_run_handler
from agentguard.models import Job, Run, RunStatus, TraceStep

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _make_run(repo: InMemoryRunRepository, *, task="q", final_state=None) -> str:
    run = Run(agent_name="test_agent", task=task, status=RunStatus.STOP, final_state=final_state or {"answer": "a"})
    await repo.create_run(run)
    await repo.save_trace_step(TraceStep(run_id=run.id, kind="function", name="format_output", outcome="success"))
    return run.id


class TestEvaluationRunHandler:
    async def test_happy_path_persists_evaluation_results(self, fake_repository):
        run_a = await _make_run(fake_repository)
        run_b = await _make_run(fake_repository)
        suite = EvaluationSuite(name="ops", metrics=[SuiteMetric(evaluator="trajectory")])
        await fake_repository.save_evaluation_suite(suite)
        await fake_repository.enqueue_job(
            Job(kind=EVALUATION_SUITE_RUN_JOB_KIND, payload={"suite_id": suite.id, "source_run_ids": [run_a, run_b]})
        )
        worker = Worker(fake_repository, {EVALUATION_SUITE_RUN_JOB_KIND: make_evaluation_run_handler(fake_repository)})

        claimed = await worker.run_once()

        assert claimed is True
        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "complete"
        eval_runs = await fake_repository.list_evaluation_runs()
        assert len(eval_runs) == 1
        results = await fake_repository.list_evaluation_results(eval_runs[0]["id"])
        assert len(results) == 2  # 1 metric x 2 cases
        assert all(r["metric"] == "trajectory" for r in results)

    async def test_missing_source_run_is_skipped_not_fatal(self, fake_repository):
        run_a = await _make_run(fake_repository)
        suite = EvaluationSuite(name="ops", metrics=[SuiteMetric(evaluator="trajectory")])
        await fake_repository.save_evaluation_suite(suite)
        await fake_repository.enqueue_job(
            Job(
                kind=EVALUATION_SUITE_RUN_JOB_KIND,
                payload={"suite_id": suite.id, "source_run_ids": [run_a, "does-not-exist"]},
            )
        )
        worker = Worker(fake_repository, {EVALUATION_SUITE_RUN_JOB_KIND: make_evaluation_run_handler(fake_repository)})

        await worker.run_once()

        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "complete"
        eval_runs = await fake_repository.list_evaluation_runs()
        results = await fake_repository.list_evaluation_results(eval_runs[0]["id"])
        assert len(results) == 1  # only run_a produced a case

    async def test_bad_suite_id_fails_immediately_without_retry(self, fake_repository):
        await fake_repository.enqueue_job(
            Job(
                kind=EVALUATION_SUITE_RUN_JOB_KIND,
                payload={"suite_id": "does-not-exist", "source_run_ids": []},
                max_attempts=5,
            )
        )
        worker = Worker(fake_repository, {EVALUATION_SUITE_RUN_JOB_KIND: make_evaluation_run_handler(fake_repository)})

        await worker.run_once()

        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "failed"
        assert jobs[0]["attempts"] == 1
        assert "does-not-exist" in jobs[0]["error"]

    async def test_metric_with_no_matching_evaluator_is_available_false_not_fatal(self, fake_repository):
        run_a = await _make_run(fake_repository)
        suite = EvaluationSuite(name="ops", metrics=[SuiteMetric(evaluator="custom.unregistered")])
        await fake_repository.save_evaluation_suite(suite)
        await fake_repository.enqueue_job(
            Job(kind=EVALUATION_SUITE_RUN_JOB_KIND, payload={"suite_id": suite.id, "source_run_ids": [run_a]})
        )
        worker = Worker(fake_repository, {EVALUATION_SUITE_RUN_JOB_KIND: make_evaluation_run_handler(fake_repository)})

        await worker.run_once()

        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "complete"
        eval_runs = await fake_repository.list_evaluation_runs()
        results = await fake_repository.list_evaluation_results(eval_runs[0]["id"])
        assert results[0]["available"] is False


def test_permanent_job_failure_is_importable_for_this_handler():
    # Sanity: the handler module re-uses the shared Worker-level exception
    # rather than defining a second, incompatible "permanent failure" type.
    assert issubclass(PermanentJobFailure, Exception)
