"""model_benchmark_run job handler (agentguard/jobs/handlers.py) —
mirrors test_evaluation_job_handler.py's coverage: happy path persists
per-model benchmark results via the existing ModelBenchmarkEngine, a
missing source run is skipped rather than failing the job, and a bad
suite_id or a bad benchmark_id each fail immediately (no wasted
retries) via PermanentJobFailure.
"""
from __future__ import annotations

import agentguard
import pytest
from agentguard.evaluation import EvaluationSuite, SuiteMetric
from agentguard.evaluation.benchmark import ModelCallResult
from agentguard.jobs import MODEL_BENCHMARK_RUN_JOB_KIND, PermanentJobFailure, Worker, make_model_benchmark_run_handler
from agentguard.models import Job, ModelBenchmark, Run, RunStatus, TraceStep

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _make_run(repo: InMemoryRunRepository, *, task="q") -> str:
    run = Run(agent_name="test_agent", task=task, status=RunStatus.STOP, final_state={"answer": "a"})
    await repo.create_run(run)
    await repo.save_trace_step(TraceStep(run_id=run.id, kind="function", name="format_output", outcome="success"))
    return run.id


async def _stub_model_call_fn(model, case):
    return ModelCallResult(actual_output=f"{model}'s answer", cost_usd=0.01, latency_ms=100.0)


class TestModelBenchmarkRunHandler:
    async def test_happy_path_persists_a_result_row_per_model(self, fake_repository):
        run_a = await _make_run(fake_repository)
        suite = EvaluationSuite(name="ops", metrics=[SuiteMetric(evaluator="trajectory")])
        await fake_repository.save_evaluation_suite(suite)
        benchmark = ModelBenchmark(suite_id=suite.id, models=["gpt-4o-mini", "claude-haiku"])
        await fake_repository.save_model_benchmark(benchmark)
        await fake_repository.enqueue_job(
            Job(
                kind=MODEL_BENCHMARK_RUN_JOB_KIND,
                payload={
                    "benchmark_id": benchmark.id,
                    "suite_id": suite.id,
                    "source_run_ids": [run_a],
                    "models": ["gpt-4o-mini", "claude-haiku"],
                },
            )
        )
        handler = make_model_benchmark_run_handler(fake_repository, model_call_fn=_stub_model_call_fn)
        worker = Worker(fake_repository, {MODEL_BENCHMARK_RUN_JOB_KIND: handler})

        claimed = await worker.run_once()

        assert claimed is True
        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "complete"
        results = await fake_repository.list_model_benchmark_results(benchmark.id)
        assert {r["model"] for r in results} == {"gpt-4o-mini", "claude-haiku"}
        # exactly the one pre-created row -- the handler didn't save a second ModelBenchmark
        assert len(await fake_repository.list_model_benchmarks()) == 1

    async def test_missing_source_run_is_skipped_not_fatal(self, fake_repository):
        run_a = await _make_run(fake_repository)
        suite = EvaluationSuite(name="ops", metrics=[SuiteMetric(evaluator="trajectory")])
        await fake_repository.save_evaluation_suite(suite)
        benchmark = ModelBenchmark(suite_id=suite.id, models=["gpt-4o-mini"])
        await fake_repository.save_model_benchmark(benchmark)
        await fake_repository.enqueue_job(
            Job(
                kind=MODEL_BENCHMARK_RUN_JOB_KIND,
                payload={
                    "benchmark_id": benchmark.id,
                    "suite_id": suite.id,
                    "source_run_ids": [run_a, "does-not-exist"],
                    "models": ["gpt-4o-mini"],
                },
            )
        )
        handler = make_model_benchmark_run_handler(fake_repository, model_call_fn=_stub_model_call_fn)
        worker = Worker(fake_repository, {MODEL_BENCHMARK_RUN_JOB_KIND: handler})

        await worker.run_once()

        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "complete"
        results = await fake_repository.list_model_benchmark_results(benchmark.id)
        assert len(results) == 1  # only run_a produced a case

    async def test_bad_suite_id_fails_immediately_without_retry(self, fake_repository):
        benchmark = ModelBenchmark(suite_id="does-not-exist", models=["gpt-4o-mini"])
        await fake_repository.save_model_benchmark(benchmark)
        await fake_repository.enqueue_job(
            Job(
                kind=MODEL_BENCHMARK_RUN_JOB_KIND,
                payload={
                    "benchmark_id": benchmark.id,
                    "suite_id": "does-not-exist",
                    "source_run_ids": [],
                    "models": ["gpt-4o-mini"],
                },
                max_attempts=5,
            )
        )
        handler = make_model_benchmark_run_handler(fake_repository, model_call_fn=_stub_model_call_fn)
        worker = Worker(fake_repository, {MODEL_BENCHMARK_RUN_JOB_KIND: handler})

        await worker.run_once()

        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "failed"
        assert jobs[0]["attempts"] == 1
        assert "does-not-exist" in jobs[0]["error"]

    async def test_bad_benchmark_id_fails_immediately_without_retry(self, fake_repository):
        suite = EvaluationSuite(name="ops", metrics=[SuiteMetric(evaluator="trajectory")])
        await fake_repository.save_evaluation_suite(suite)
        await fake_repository.enqueue_job(
            Job(
                kind=MODEL_BENCHMARK_RUN_JOB_KIND,
                payload={
                    "benchmark_id": "does-not-exist",
                    "suite_id": suite.id,
                    "source_run_ids": [],
                    "models": ["gpt-4o-mini"],
                },
                max_attempts=5,
            )
        )
        handler = make_model_benchmark_run_handler(fake_repository, model_call_fn=_stub_model_call_fn)
        worker = Worker(fake_repository, {MODEL_BENCHMARK_RUN_JOB_KIND: handler})

        await worker.run_once()

        jobs = await fake_repository.list_jobs()
        assert jobs[0]["status"] == "failed"
        assert "does-not-exist" in jobs[0]["error"]
