"""Cross-run failure clustering (agentguard/reliability/cluster.py).

Covers: minimum-cluster-size filtering, trace-exception clustering at
both confidence tiers, fallback to the root-cause-explanation signal,
an explainable/reproducible priority score, recency decay, persisted
triage status surviving recompute, propose-fix reusing the existing
improvement pipeline, and workspace isolation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import agentguard
from agentguard.improve.optimizer import DeterministicTestOptimizer
from agentguard.models import RootCause, Run, RunStatus, TraceStep
from agentguard.reliability.cluster import MIN_CLUSTER_SIZE, FailureClusterEngine

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _make_run(
    repo: InMemoryRunRepository, *, started_at: datetime, workspace_id: str | None = None
) -> str:
    run = Run(agent_name="test_agent", status=RunStatus.STOP, started_at=started_at, workspace_id=workspace_id)
    await repo.create_run(run)
    return run.id


async def _fail_with_exception(
    repo: InMemoryRunRepository, run_id: str, *, exception_type: str, code_file: str | None, code_function: str | None
) -> None:
    step = TraceStep(
        run_id=run_id, kind="function", name="do_thing", outcome="failure",
        exception_type=exception_type, exception_message="boom",
        code_file=code_file, code_function=code_function,
    )
    await repo.save_trace_step(step)


async def _fail_with_root_cause(repo: InMemoryRunRepository, run_id: str, *, explanation: str, confidence: float) -> None:
    root_cause = RootCause(run_id=run_id, earliest_deviation="S2", explanation=explanation, confidence=confidence)
    await repo.save_root_cause(run_id, root_cause)


NOW = datetime.now(timezone.utc)


async def test_single_failure_not_reported_below_min_cluster_size(fake_repository):
    assert MIN_CLUSTER_SIZE == 2
    run_id = await _make_run(fake_repository, started_at=NOW)
    await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file="a.py", code_function="f")

    clusters = await FailureClusterEngine(fake_repository).cluster()
    assert clusters == []


async def test_two_runs_same_exception_and_location_cluster_with_high_confidence(fake_repository):
    run_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1))
    run_b = await _make_run(fake_repository, started_at=NOW)
    for run_id in (run_a, run_b):
        await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file="a.py", code_function="f")

    clusters = await FailureClusterEngine(fake_repository).cluster()
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.signal == "trace_exception"
    assert cluster.count == 2
    assert cluster.confidence == 0.95
    assert set(cluster.run_ids) == {run_a, run_b}
    assert cluster.representative_run_id == run_b  # most recent


async def test_exception_only_match_gets_lower_confidence(fake_repository):
    run_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1))
    run_b = await _make_run(fake_repository, started_at=NOW)
    for run_id in (run_a, run_b):
        await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file=None, code_function=None)

    clusters = await FailureClusterEngine(fake_repository).cluster()
    assert len(clusters) == 1
    assert clusters[0].confidence == 0.6


async def test_fallback_to_root_cause_explanation_when_no_trace_step_failure(fake_repository):
    run_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1))
    run_b = await _make_run(fake_repository, started_at=NOW)
    for run_id, conf in ((run_a, 0.9), (run_b, 0.7)):
        await _fail_with_root_cause(fake_repository, run_id, explanation="max_budget disappeared", confidence=conf)

    clusters = await FailureClusterEngine(fake_repository).cluster()
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.signal == "root_cause_explanation"
    assert cluster.root_cause_explanation == "max_budget disappeared"
    assert cluster.confidence == pytest.approx(0.8)  # mean(0.9, 0.7)


async def test_priority_score_is_explainable(fake_repository):
    run_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1))
    run_b = await _make_run(fake_repository, started_at=NOW)
    for run_id in (run_a, run_b):
        await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file="a.py", code_function="f")

    clusters = await FailureClusterEngine(fake_repository).cluster()
    cluster = clusters[0]
    f, w = cluster.priority_factors, cluster.priority_weights
    norm_count = min(f["count"] / 10, 1.0)
    recency_factor = max(0.0, 1.0 - f["recency_days"] / 30)
    expected = w["count"] * norm_count + w["confidence"] * f["confidence"] + w["recency"] * recency_factor
    assert cluster.priority_score == pytest.approx(expected)


async def test_recency_decays_priority(fake_repository):
    recent_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1))
    recent_b = await _make_run(fake_repository, started_at=NOW)
    old_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=41))
    old_b = await _make_run(fake_repository, started_at=NOW - timedelta(days=40))
    for run_id in (recent_a, recent_b):
        await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file="a.py", code_function="f")
    for run_id in (old_a, old_b):
        await _fail_with_exception(fake_repository, run_id, exception_type="KeyError", code_file="b.py", code_function="g")

    clusters = await FailureClusterEngine(fake_repository).cluster()
    by_signal = {c.exception_type: c for c in clusters}
    assert by_signal["ValueError"].priority_score > by_signal["KeyError"].priority_score
    assert by_signal["KeyError"].priority_factors["recency_days"] > 30


async def test_persisted_status_survives_recompute(fake_repository):
    run_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1))
    run_b = await _make_run(fake_repository, started_at=NOW)
    for run_id in (run_a, run_b):
        await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file="a.py", code_function="f")

    clusters = await FailureClusterEngine(fake_repository).cluster()
    cluster_key = clusters[0].cluster_key
    await fake_repository.save_problem_status(None, cluster_key, "acknowledged", triaged_by="dev@example.com")

    run_c = await _make_run(fake_repository, started_at=NOW + timedelta(days=1))
    await _fail_with_exception(fake_repository, run_c, exception_type="ValueError", code_file="a.py", code_function="f")

    clusters = await FailureClusterEngine(fake_repository).cluster()
    assert clusters[0].count == 3
    assert clusters[0].status == "acknowledged"
    assert clusters[0].triaged_by == "dev@example.com"


async def test_propose_fix_reuses_existing_improvement_pipeline(fake_repository):
    from agentguard.improve.workflow import propose_improvement

    run_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1))
    run_b = await _make_run(fake_repository, started_at=NOW)
    for run_id in (run_a, run_b):
        await _fail_with_root_cause(fake_repository, run_id, explanation="max_budget disappeared", confidence=0.9)

    clusters = await FailureClusterEngine(fake_repository).cluster()
    cluster = clusters[0]

    candidate, _ = await propose_improvement(
        fake_repository, cluster.representative_run_id, corpus_run_ids=cluster.run_ids,
        optimizer=DeterministicTestOptimizer(),
    )
    await fake_repository.save_problem_status(
        None, cluster.cluster_key, "fix_proposed", linked_candidate_id=candidate.id, triaged_by="dev@example.com"
    )

    stored = await fake_repository.get_improvement_candidate(candidate.id)
    assert stored is not None
    assert stored["source_run_id"] == cluster.representative_run_id

    clusters = await FailureClusterEngine(fake_repository).cluster()
    assert clusters[0].status == "fix_proposed"
    assert clusters[0].linked_candidate_id == candidate.id


async def test_workspace_isolation(fake_repository):
    run_a = await _make_run(fake_repository, started_at=NOW - timedelta(days=1), workspace_id="ws-a")
    run_b = await _make_run(fake_repository, started_at=NOW, workspace_id="ws-a")
    run_c = await _make_run(fake_repository, started_at=NOW, workspace_id="ws-b")
    run_d = await _make_run(fake_repository, started_at=NOW, workspace_id="ws-b")
    for run_id in (run_a, run_b):
        await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file="a.py", code_function="f")
    for run_id in (run_c, run_d):
        await _fail_with_exception(fake_repository, run_id, exception_type="ValueError", code_file="a.py", code_function="f")

    clusters_a = await FailureClusterEngine(fake_repository).cluster(workspace_id="ws-a")
    assert len(clusters_a) == 1
    assert set(clusters_a[0].run_ids) == {run_a, run_b}

    clusters_b = await FailureClusterEngine(fake_repository).cluster(workspace_id="ws-b")
    assert len(clusters_b) == 1
    assert set(clusters_b[0].run_ids) == {run_c, run_d}
