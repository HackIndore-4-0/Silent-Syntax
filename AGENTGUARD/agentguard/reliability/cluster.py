"""Cross-run failure clustering — the Improvement Engine's "problems" view.

Groups failed runs across the WHOLE history (not just one run's root
cause) into prioritized "problems," recomputed fresh from persisted
Run/RootCause/TraceStep data on every request — same "never cache a
derived aggregate" principle as tool_profile.py/fingerprint.py. Only a
human's triage decision (status/linked_candidate_id/triaged_by) is ever
persisted (agentguard_problems) — membership, count, confidence, and
priority are always recomputed, never stale.

Clustering signal, most-specific first:
1. The run's earliest failing TraceStep (kind-agnostic — a failed
   @traceable function or a failed LLM call both count), keyed on
   (exception_type, code_file, code_function) — value-independent, so
   two runs failing on the same bug with different inputs still group
   together, unlike a root-cause explanation string which embeds
   run-specific numbers.
2. Falling back to the run's persisted RootCause.explanation (the same
   signal /api/v2/failure-patterns already uses) when no TraceStep
   failure was recorded for that run.
3. Otherwise the run isn't clustered at all — no fabricated grouping.

MIN_CLUSTER_SIZE=2, not tool_profile.py's MIN_SAMPLE_SIZE=5: this is
exact-key equality grouping, not a statistical estimate. Two runs
sharing an identical key is already 100% real evidence of a recurring
problem, with no sampling error to guard against — a cluster of 1 is
excluded only because "recurring" is definitionally false for a single
occurrence.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from ..models import FailureCluster

MIN_CLUSTER_SIZE = 2
_TRACE_EXACT_MATCH_CONFIDENCE = 0.95
_TRACE_EXCEPTION_ONLY_CONFIDENCE = 0.6
_RECENCY_DECAY_DAYS = 30.0
_PRIORITY_WEIGHTS = {"count": 0.5, "confidence": 0.3, "recency": 0.2}


def _cluster_key(signal: str, *parts: str) -> str:
    digest_input = "|".join([signal, *parts])
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]


def _earliest_failing_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    for step in steps:
        if step.get("outcome") == "failure" and step.get("exception_type"):
            return step
    return None


def _signal_key_for_run(
    *,
    root_cause: dict[str, Any] | None,
    trace_steps: list[dict[str, Any]],
) -> tuple[str, ...] | None:
    failing_step = _earliest_failing_step(trace_steps)
    if failing_step is not None:
        return (
            "trace_exception",
            failing_step["exception_type"],
            failing_step.get("code_file") or "",
            failing_step.get("code_function") or "",
        )
    if root_cause is not None:
        return ("root_cause_explanation", root_cause["explanation"])
    return None


def _explanation_for(key: tuple[str, ...], count: int) -> str:
    if key[0] == "trace_exception":
        _, exception_type, code_file, code_function = key
        if code_file and code_function:
            return f"{count} runs raised {exception_type} in {code_function} ({code_file})"
        return f"{count} runs raised {exception_type} (no consistent code location recorded)"
    _, explanation = key
    return f"{count} runs: {explanation}"


def build_failure_clusters(
    *,
    runs: list[dict[str, Any]],
    root_causes_by_run: dict[str, dict[str, Any] | None],
    trace_steps_by_run: dict[str, list[dict[str, Any]]],
    problem_statuses: dict[str, dict[str, Any]],
    workspace_id: str | None,
    now: datetime,
) -> list[FailureCluster]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for run in runs:
        key = _signal_key_for_run(
            root_cause=root_causes_by_run.get(run["id"]),
            trace_steps=trace_steps_by_run.get(run["id"], []),
        )
        if key is None:
            continue
        groups.setdefault(key, []).append(run)

    clusters: list[FailureCluster] = []
    for key, members in groups.items():
        count = len(members)
        if count < MIN_CLUSTER_SIZE:
            continue

        members_sorted = sorted(members, key=lambda r: r["started_at"])
        first_seen = members_sorted[0]["started_at"]
        last_seen = members_sorted[-1]["started_at"]
        representative_run_id = members_sorted[-1]["id"]
        run_ids = [r["id"] for r in members_sorted]

        if key[0] == "trace_exception":
            _, exception_type, code_file, code_function = key
            confidence = (
                _TRACE_EXACT_MATCH_CONFIDENCE
                if code_file and code_function
                else _TRACE_EXCEPTION_ONLY_CONFIDENCE
            )
            root_cause_explanation = None
        else:
            exception_type = None
            code_file = None
            code_function = None
            confidences = [
                root_causes_by_run[r["id"]]["confidence"]
                for r in members_sorted
                if root_causes_by_run.get(r["id"]) is not None
            ]
            confidence = sum(confidences) / len(confidences) if confidences else 0.0
            root_cause_explanation = key[1]

        days_since_last_seen = max(0.0, (now - last_seen).total_seconds() / 86400)
        norm_count = min(count / 10, 1.0)
        recency_factor = max(0.0, 1.0 - days_since_last_seen / _RECENCY_DECAY_DAYS)
        priority_score = (
            _PRIORITY_WEIGHTS["count"] * norm_count
            + _PRIORITY_WEIGHTS["confidence"] * confidence
            + _PRIORITY_WEIGHTS["recency"] * recency_factor
        )

        cluster_key = _cluster_key(*key)
        status_row = problem_statuses.get(cluster_key)

        clusters.append(
            FailureCluster(
                cluster_key=cluster_key,
                workspace_id=workspace_id,
                signal=key[0],  # type: ignore[arg-type]
                exception_type=exception_type,
                code_file=code_file,
                code_function=code_function,
                root_cause_explanation=root_cause_explanation,
                explanation=_explanation_for(key, count),
                count=count,
                run_ids=run_ids,
                representative_run_id=representative_run_id,
                first_seen=first_seen,
                last_seen=last_seen,
                confidence=confidence,
                priority_score=priority_score,
                priority_factors={
                    "count": float(count),
                    "confidence": confidence,
                    "recency_days": days_since_last_seen,
                },
                priority_weights=dict(_PRIORITY_WEIGHTS),
                status=status_row["status"] if status_row else "open",
                linked_candidate_id=status_row.get("linked_candidate_id") if status_row else None,
                triaged_by=status_row.get("triaged_by") if status_row else None,
                triaged_at=status_row.get("triaged_at") if status_row else None,
            )
        )

    clusters.sort(key=lambda c: c.priority_score, reverse=True)
    return clusters


class FailureClusterEngine:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def cluster(self, *, workspace_id: str | None = None) -> list[FailureCluster]:
        runs = await self._repository.list_runs(limit=10_000, workspace_id=workspace_id)
        root_causes_by_run = {r["id"]: await self._repository.get_root_cause(r["id"]) for r in runs}
        trace_steps_by_run = {r["id"]: await self._repository.list_trace_steps_for_run(r["id"]) for r in runs}
        statuses = await self._repository.list_problem_statuses(workspace_id=workspace_id)
        problem_statuses = {s["cluster_key"]: s for s in statuses}

        return build_failure_clusters(
            runs=runs,
            root_causes_by_run=root_causes_by_run,
            trace_steps_by_run=trace_steps_by_run,
            problem_statuses=problem_statuses,
            workspace_id=workspace_id,
            now=datetime.now(timezone.utc),
        )
