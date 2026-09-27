"""Regression Corpus — Phase 4.

A collection of historical, already-persisted runs used as a fixed
regression fixture: "replay this corpus against a candidate agent,
evaluate the candidate, compare against the baseline." Every corpus
entry is built FROM a real, previously-persisted run (Rule: "Do not
fabricate historical runs") — `RegressionCorpus.from_run_ids()` reads
each run's actual `task`/`policy` back out of storage rather than
accepting invented ones.

Feeds both the CI/CD reliability gate (agentguard/cli, `ci-gate`) and
the auto-improvement candidate validation flow
(agentguard/improve/workflow.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..models import Policy
from .compare import ComparisonResult, build_report_for_run, compare_metrics, mean_metrics, report_metrics

AgentCallable = Callable[[str], Awaitable[str]]
"""A candidate-evaluation callable: given a corpus entry's `task`, it
executes the candidate agent (a real @monitor-wrapped call) and returns
the NEW run_id that call produced — never a guess at "the most recent
run", so evaluate_candidate never risks attributing the wrong run."""


@dataclass
class CorpusEntry:
    baseline_run_id: str
    task: str
    policy: Policy


@dataclass
class CorpusEvaluation:
    baseline_run_ids: list[str]
    candidate_run_ids: list[str]
    comparison: ComparisonResult


@dataclass
class RegressionCorpus:
    entries: list[CorpusEntry] = field(default_factory=list)

    @classmethod
    async def from_run_ids(cls, repository: Any, run_ids: list[str]) -> "RegressionCorpus":
        entries = []
        for run_id in run_ids:
            run = await repository.get_run(run_id)
            if run is None:
                raise ValueError(f"run {run_id!r} not found — refusing to fabricate a corpus entry")
            policy_data = run.get("policy") or {}
            policy = Policy(**{k: v for k, v in policy_data.items() if k in Policy.model_fields})
            entries.append(CorpusEntry(baseline_run_id=run_id, task=run.get("task") or "", policy=policy))
        return cls(entries=entries)

    @property
    def baseline_run_ids(self) -> list[str]:
        return [e.baseline_run_id for e in self.entries]

    async def evaluate_candidate(self, repository: Any, agent_fn: AgentCallable) -> CorpusEvaluation:
        """Re-executes `agent_fn(entry.task)` once per corpus entry (each
        a fresh, real, persisted run — never simulated), then compares
        the resulting candidate runs' AGGREGATE (mean) metrics against
        the corpus's baseline runs' aggregate metrics."""
        candidate_run_ids: list[str] = []
        candidate_metrics = []
        baseline_metrics = []

        for entry in self.entries:
            candidate_run_id = await agent_fn(entry.task)
            candidate_run_ids.append(candidate_run_id)

            baseline_run, baseline_report = await build_report_for_run(repository, entry.baseline_run_id)
            baseline_metrics.append(report_metrics(baseline_run, baseline_report))

            candidate_run, candidate_report = await build_report_for_run(repository, candidate_run_id)
            candidate_metrics.append(report_metrics(candidate_run, candidate_report))

        return CorpusEvaluation(
            baseline_run_ids=self.baseline_run_ids,
            candidate_run_ids=candidate_run_ids,
            comparison=compare_metrics(mean_metrics(baseline_metrics), mean_metrics(candidate_metrics), label_a="baseline", label_b="candidate"),
        )


async def evaluate_run_batches(
    repository: Any, baseline_run_ids: list[str], candidate_run_ids: list[str], *, label_a: str = "baseline", label_b: str = "candidate"
) -> ComparisonResult:
    """The core batch-comparison primitive: two lists of already-executed
    run_ids (a baseline batch and a candidate batch), aggregated into
    mean metrics and compared. Used directly by `agentguard ci-gate` and
    `agentguard.improve.workflow`."""
    baseline_metrics = []
    for run_id in baseline_run_ids:
        run, report = await build_report_for_run(repository, run_id)
        baseline_metrics.append(report_metrics(run, report))

    candidate_metrics = []
    for run_id in candidate_run_ids:
        run, report = await build_report_for_run(repository, run_id)
        candidate_metrics.append(report_metrics(run, report))

    return compare_metrics(mean_metrics(baseline_metrics), mean_metrics(candidate_metrics), label_a=label_a, label_b=label_b)
