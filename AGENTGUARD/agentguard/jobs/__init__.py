"""Async job queue (Phase 5) — a durable alternative to the in-process
`asyncio.create_task` background trace-write
(agentguard/tracing/_pending.py) for work that can run for minutes to
hours and must survive a process restart. See Worker's own docstring
for why this is a separate mechanism, not another `create_task` call.
"""
from ..models import Job, JobStatus
from .handlers import (
    DATASET_VALIDATION_JOB_KIND,
    EVALUATION_SUITE_RUN_JOB_KIND,
    MODEL_BENCHMARK_RUN_JOB_KIND,
    make_dataset_validation_handler,
    make_evaluation_run_handler,
    make_model_benchmark_run_handler,
)
from .worker import JobHandler, PermanentJobFailure, Worker

__all__ = [
    "Job",
    "JobStatus",
    "Worker",
    "JobHandler",
    "PermanentJobFailure",
    "DATASET_VALIDATION_JOB_KIND",
    "make_dataset_validation_handler",
    "EVALUATION_SUITE_RUN_JOB_KIND",
    "make_evaluation_run_handler",
    "MODEL_BENCHMARK_RUN_JOB_KIND",
    "make_model_benchmark_run_handler",
]
