"""Job handlers — Phase 5's first concrete job kind: dataset
validation, the use case that motivated the queue (see Worker's
docstring for why a large validation run needs a durable queue rather
than the in-process `_pending` background-task tracker).
"""
from __future__ import annotations

from typing import Any

from ..evaluation.dataset.sources.base import EvidenceSource
from ..evaluation.dataset.validator import GoldenDatasetValidator, JudgeFn
from ..models import DatasetExample
from ..storage.repository import RunRepository
from .worker import JobHandler

DATASET_VALIDATION_JOB_KIND = "dataset_validation"


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
