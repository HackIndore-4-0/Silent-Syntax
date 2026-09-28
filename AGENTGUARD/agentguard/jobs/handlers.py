"""Job handlers — Phase 5's first concrete job kind: dataset
validation, the use case that motivated the queue (see Worker's
docstring for why a large validation run needs a durable queue rather
than the in-process `_pending` background-task tracker).
"""
from __future__ import annotations

from typing import Any

from ..evaluation.benchmark import ModelBenchmarkEngine, ModelCallFn
from ..evaluation.dataset.sources.base import EvidenceSource
from ..evaluation.dataset.validator import GoldenDatasetValidator, JudgeFn
from ..evaluation.engine import EvaluationEngine, build_eval_case_from_run
from ..evaluation.evaluators.base import EvalCase
from ..evaluation.registry import build_suite_evaluator_registry
from ..models import DatasetExample, EvaluationSuite, ModelBenchmark
from ..storage.repository import RunRepository
from .worker import JobHandler, PermanentJobFailure

DATASET_VALIDATION_JOB_KIND = "dataset_validation"
EVALUATION_SUITE_RUN_JOB_KIND = "evaluation_suite_run"
MODEL_BENCHMARK_RUN_JOB_KIND = "model_benchmark_run"


def make_dataset_validation_handler(
    repository: RunRepository,
    source: EvidenceSource,
    judge: JudgeFn,
    *,
    adversarial_judge: JudgeFn | None = None,
) -> JobHandler:
    """`source`/`judge` are live objects (a DB connection pool, a judge-
    model client) that cannot round-trip through a JSONB job payload —
    they're bound into this closure at worker-startup time, exactly how
    any real job-queue handler registration works. The payload itself
    carries only plain data: which dataset_version_id to validate."""
    validator = GoldenDatasetValidator(repository, source, judge, adversarial_judge=adversarial_judge)

    async def handler(payload: dict[str, Any]) -> None:
        dataset_version_id = payload["dataset_version_id"]
        examples = await repository.list_dataset_examples(dataset_version_id)
        for example_dict in examples:
            example = DatasetExample(**example_dict)
            await validator.validate_example(example)

    return handler


def make_evaluation_run_handler(
    repository: RunRepository,
    *,
    default_judge_model: Any = None,
) -> JobHandler:
    """Builds a fresh, suite-scoped evaluator registry per job invocation
    (see build_suite_evaluator_registry) since the suite's own thresholds/
    config are only known once the payload's suite_id is loaded -- unlike
    dataset_validation's source/judge, there is nothing evaluation-specific
    to bind at worker-startup time here."""

    async def handler(payload: dict[str, Any]) -> None:
        suite_dict = await repository.get_evaluation_suite(payload["suite_id"])
        if suite_dict is None:
            raise PermanentJobFailure(f"evaluation suite '{payload['suite_id']}' not found")
        suite = EvaluationSuite(**suite_dict)

        cases: list[EvalCase] = []
        for run_id in payload["source_run_ids"]:
            run = await repository.get_run(run_id)
            if run is None:
                continue  # a since-deleted/unreachable run: skip, don't fail the whole job
            steps = await repository.list_trace_steps_for_run(run_id)
            cases.append(build_eval_case_from_run(run, steps))

        evaluators = build_suite_evaluator_registry(suite, repository=repository, default_model=default_judge_model)
        engine = EvaluationEngine(repository, evaluators)
        await engine.run_suite(suite, cases, workspace_id=payload.get("workspace_id"))

    return handler


def make_model_benchmark_run_handler(
    repository: RunRepository,
    *,
    model_call_fn: ModelCallFn,
    default_judge_model: Any = None,
) -> JobHandler:
    """Runs a ModelBenchmarkEngine.run() (real LLM calls to every candidate
    model in payload["models"], real evaluations, real cost/latency) as a
    durable background job — a benchmark can take minutes across N models
    x M cases, same rationale as make_evaluation_run_handler's own docstring.

    The dashboard API route (POST /api/v2/benchmarks) already constructed
    and saved the ModelBenchmark row synchronously before enqueueing this
    job, so it could hand the benchmark_id back to the caller immediately;
    this handler loads that same row and passes it into .run(benchmark=...)
    so a second row is never created for one benchmark request."""

    async def handler(payload: dict[str, Any]) -> None:
        suite_dict = await repository.get_evaluation_suite(payload["suite_id"])
        if suite_dict is None:
            raise PermanentJobFailure(f"evaluation suite '{payload['suite_id']}' not found")
        suite = EvaluationSuite(**suite_dict)

        benchmark_dict = await repository.get_model_benchmark(payload["benchmark_id"])
        if benchmark_dict is None:
            raise PermanentJobFailure(f"model benchmark '{payload['benchmark_id']}' not found")
        benchmark = ModelBenchmark(**benchmark_dict)

        cases: list[EvalCase] = []
        for run_id in payload["source_run_ids"]:
            run = await repository.get_run(run_id)
            if run is None:
                continue  # a since-deleted/unreachable run: skip, don't fail the whole job
            steps = await repository.list_trace_steps_for_run(run_id)
            cases.append(build_eval_case_from_run(run, steps))

        evaluators = build_suite_evaluator_registry(suite, repository=repository, default_model=default_judge_model)
        evaluation_engine = EvaluationEngine(repository, evaluators)
        benchmark_engine = ModelBenchmarkEngine(repository, evaluation_engine, model_call_fn)
        await benchmark_engine.run(
            suite, cases, payload["models"],
            dataset_version_id=payload.get("dataset_version_id"),
            workspace_id=payload.get("workspace_id"),
            benchmark=benchmark,
        )

    return handler
