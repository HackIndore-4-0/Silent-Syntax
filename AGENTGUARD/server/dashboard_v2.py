"""Dashboard V2 — the authenticated, workspace-scoped data API the new
dashboard UI consumes.

Every route here requires authentication (`Depends(get_current_workspace_id)`)
and filters/verifies ownership against that workspace_id — this is where
"User A must never be able to retrieve User B's runs/traces/evaluations/
policies/audit logs/API keys/agents" is actually enforced (Rule:
"Enforce isolation at API/database query level, not only frontend
filtering"). A run, policy, or candidate that exists but belongs to a
different workspace returns 404 — identical to "does not exist" from the
caller's point of view, never a 403 that would confirm something exists
in another tenant's data.

Reuses every existing engine (`build_reliability_report`, `replay`,
`compare_runs`, `ToolProfileEngine`, `FingerprintEngine`,
`verify_audit_chain`, `PolicyRegistry`, `rollback`) — no duplicated
business logic, only the ownership check and the workspace_id filter are
new.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from agentguard._runtime import get_repository
from agentguard.a2a.card import build_agent_card
from agentguard.audit.chain import verify_audit_chain
from agentguard.policy.registry import PolicyRegistry, PolicyRegistryError
from agentguard.improve.workflow import ApprovalError, propose_improvement
from agentguard.recovery import RollbackError, rollback
from agentguard.regression.compare import compare_runs
from agentguard.reliability.cluster import FailureClusterEngine
from agentguard.reliability.fingerprint import FingerprintEngine
from agentguard.reliability.model_profile import ModelProfileEngine
from agentguard.reliability.report import build_reliability_report
from agentguard.reliability.tool_profile import ToolProfileEngine
from agentguard.replay import replay as replay_run
from agentguard.evaluation.diagnose import diagnose_evaluation_failure
from agentguard.evaluation.recommend import EvaluationRecommendationEngine
from agentguard.jobs import EVALUATION_SUITE_RUN_JOB_KIND
from agentguard.models import EvaluationSuite, Job, SuiteMetric, ToolAlternative
from agentguard.skills import (
    SkillRequest,
    UnknownCategoryError,
    UnknownFrameworkError,
    compose_skill,
    list_skill_options,
)
from pydantic import BaseModel

from .auth import get_current_workspace_id

router = APIRouter(prefix="/api/v2")


async def _owned_run(workspace_id: str, run_id: str) -> dict[str, Any]:
    """Loads a run and verifies it belongs to `workspace_id`. Raises 404
    (not 403) for either "doesn't exist" or "belongs to someone else" —
    indistinguishable from the outside, which is the point."""
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None or run.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return ordered[idx]


# -- Overview -----------------------------------------------------------------


@router.get("/overview")
async def overview(
    workspace_id: str = Depends(get_current_workspace_id),
    project_id: str | None = None,
    agent_name: str | None = None,
    hours: int = Query(24 * 7, ge=1, le=24 * 90),
):
    repository = get_repository()
    all_runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    runs = [
        r for r in all_runs
        if r["started_at"] >= cutoff
        and (project_id is None or r.get("project_id") == project_id)
        and (agent_name is None or r.get("agent_name") == agent_name)
    ]

    total = len(runs)
    succeeded = sum(1 for r in runs if r["status"] == "continue")
    success_rate = (succeeded / total) if total else None
    durations = [r["duration_ms"] for r in runs if r.get("duration_ms") is not None]
    avg_latency = statistics.mean(durations) if durations else None
    total_tokens = sum(
        (r.get("tokens_input") or 0) + (r.get("tokens_output") or 0) for r in runs if r.get("tokens_input") or r.get("tokens_output")
    )
    any_tokens = any(r.get("tokens_input") or r.get("tokens_output") for r in runs)
    total_cost = sum(r["estimated_cost_usd"] for r in runs if r.get("estimated_cost_usd") is not None)
    any_cost = any(r.get("estimated_cost_usd") is not None for r in runs)

    # Reliability score: mean of behavioral_reliability across runs that
    # have a computable Reliability Report — never fabricated when there
    # is no data.
    reliability_scores = []
    dim_totals: dict[str, list[float]] = {
        "goal_completion": [], "constraint_adherence": [], "tool_usage": [],
        "decision_consistency": [], "correctness": [], "behavioral_reliability": [],
    }
    for r in runs:
        risk_assessments = await repository.list_risk_assessments(r["id"])
        root_cause = await repository.get_root_cause(r["id"])
        report = build_reliability_report(r, risk_assessments=risk_assessments, root_cause=root_cause)
        d = report.to_dict()["dimensions"]
        if d["behavioral_reliability"]["available"]:
            reliability_scores.append(d["behavioral_reliability"]["value"])
        for key in dim_totals:
            if d[key]["available"]:
                dim_totals[key].append(d[key]["value"])

    reliability_score = statistics.mean(reliability_scores) if reliability_scores else None

    # Run volume over time — daily buckets across the requested window.
    buckets: dict[str, int] = {}
    for r in runs:
        day = r["started_at"].date().isoformat()
        buckets[day] = buckets.get(day, 0) + 1
    volume_series = [{"date": d, "count": c} for d, c in sorted(buckets.items())]

    recent = sorted(runs, key=lambda r: r["started_at"], reverse=True)[:20]

    return {
        "kpis": {
            "total_runs": total,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "total_tokens": total_tokens if any_tokens else None,
            "estimated_cost_usd": total_cost if any_cost else None,
            "reliability_score": reliability_score,
        },
        "charts": {
            "run_volume": volume_series,
            "latency_percentiles": {
                "p50": _percentile(durations, 0.5),
                "p95": _percentile(durations, 0.95),
                "p99": _percentile(durations, 0.99),
            },
            "success_failure": {"success": succeeded, "failure": total - succeeded},
        },
        "evaluation_summary": {k: (statistics.mean(v) if v else None) for k, v in dim_totals.items()},
        "recent_runs": [
            {
                "id": r["id"],
                "agent_name": r["agent_name"],
                "project_id": r.get("project_id"),
                "status": r["status"],
                "duration_ms": r.get("duration_ms"),
                "tokens": (r.get("tokens_input") or 0) + (r.get("tokens_output") or 0) if (r.get("tokens_input") or r.get("tokens_output")) else None,
                "started_at": r["started_at"],
            }
            for r in recent
        ],
    }


# -- Runs / Traces --------------------------------------------------------------


@router.get("/runs")
async def list_runs_v2(
    workspace_id: str = Depends(get_current_workspace_id),
    q: str | None = None,
    agent_name: str | None = None,
    project_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = 0,
):
    repository = get_repository()
    runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)
    if agent_name:
        runs = [r for r in runs if r["agent_name"] == agent_name]
    if project_id:
        runs = [r for r in runs if r.get("project_id") == project_id]
    if status:
        runs = [r for r in runs if r["status"] == status]
    if q:
        needle = q.lower()
        runs = [r for r in runs if needle in (r.get("task") or "").lower() or needle in r["id"].lower()]
    return {"total": len(runs), "runs": runs[offset : offset + limit]}


@router.get("/runs/{run_id}")
async def get_run_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    return await _owned_run(workspace_id, run_id)


@router.get("/runs/{run_id}/reliability-report")
async def get_reliability_report_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    run = await _owned_run(workspace_id, run_id)
    risk_assessments = await repository.list_risk_assessments(run_id)
    root_cause = await repository.get_root_cause(run_id)
    audit_events = await repository.list_audit_events(run_id)
    report = build_reliability_report(run, risk_assessments=risk_assessments, root_cause=root_cause, audit_events=audit_events)
    return report.to_dict()


@router.get("/runs/{run_id}/timeline")
async def get_timeline_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    events = await repository.list_audit_events(run_id)
    return {"run_id": run_id, "events": events}


@router.get("/runs/{run_id}/trace-steps")
async def get_trace_steps_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    """Fine-grained @traceable / wrap_llm_client call tree (agentguard/tracing/)
    for `run_id` — distinct from /timeline's coarser audit-event log. Each
    step carries its own input/output, latency, and parent_step_id nesting."""
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    steps = await repository.list_trace_steps_for_run(run_id)
    return {"run_id": run_id, "steps": steps}


@router.get("/runs/{run_id}/checkpoints")
async def get_checkpoints_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    return await repository.list_checkpoints(run_id)


@router.get("/runs/{run_id}/counterfactual")
async def get_counterfactual_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    result = await repository.get_counterfactual(run_id)
    return result or {"counterfactual": None}


@router.get("/runs/{run_id}/audit")
async def get_audit_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    events = await repository.list_audit_events(run_id)
    return {"run_id": run_id, "event_count": len(events), "events": events}


@router.post("/runs/{run_id}/audit/verify")
async def verify_audit_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    return await verify_audit_chain(repository, run_id)


@router.get("/runs/{run_id}/replay")
async def get_replay_v2(run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    session = await replay_run(repository, run_id)
    return {
        "run_id": session.run_id,
        "mode": session.mode,
        "no_external_side_effects": session.no_external_side_effects,
        "task": session.task,
        "policy_version": session.policy_version,
        "agent_version": session.agent_version,
        "root_cause": session.root_cause,
        "risk": session.risk,
        "confidence": session.confidence,
        "steps": [{"step": s.step, "label": s.label, "kind": s.kind, "data": s.data, "timestamp": s.timestamp} for s in session.steps],
    }


class RollbackRequestV2(BaseModel):
    to_checkpoint: str


@router.post("/runs/{run_id}/rollback")
async def rollback_v2(run_id: str, body: RollbackRequestV2, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    await _owned_run(workspace_id, run_id)
    try:
        result = await rollback(repository, run_id, body.to_checkpoint)
    except RollbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "checkpoint": result.checkpoint.model_dump(mode="json"),
        "previous_state": result.previous_state,
        "restored_state": result.restored_state,
        "decision": result.decision.model_dump(mode="json"),
    }


@router.get("/runs/compare")
async def compare_v2(run_a: str, run_b: str, workspace_id: str = Depends(get_current_workspace_id)):
    await _owned_run(workspace_id, run_a)
    await _owned_run(workspace_id, run_b)
    repository = get_repository()
    result = await compare_runs(repository, run_a, run_b)
    return result.to_dict()


# -- Latency ----------------------------------------------------------------------


@router.get("/latency")
async def latency_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)
    durations = [r["duration_ms"] for r in runs if r.get("duration_ms") is not None]

    def _breakdown(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[float]] = {}
        for r in runs:
            value = r.get(key)
            if value is None or r.get("duration_ms") is None:
                continue
            groups.setdefault(str(value), []).append(r["duration_ms"])
        return [
            {"key": k, "p50": _percentile(v, 0.5), "p95": _percentile(v, 0.95), "avg": statistics.mean(v), "count": len(v)}
            for k, v in groups.items()
        ]

    return {
        "overall": {
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
            "avg": statistics.mean(durations) if durations else None,
            "sample_count": len(durations),
        },
        "by_agent": _breakdown("agent_name"),
        "by_project": _breakdown("project_id"),
        "by_model": _breakdown("model_name"),
    }


# -- Tokens & Cost ------------------------------------------------------------------


@router.get("/tokens")
async def tokens_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)
    with_tokens = [r for r in runs if r.get("tokens_input") is not None or r.get("tokens_output") is not None]

    if not with_tokens:
        return {
            "available": False,
            "detail": "No token/cost metadata has been reported for any run in this workspace.",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "estimated_cost_usd": None,
            "by_agent": [],
            "by_model": [],
            "by_project": [],
            "over_time": [],
        }

    input_total = sum(r.get("tokens_input") or 0 for r in with_tokens)
    output_total = sum(r.get("tokens_output") or 0 for r in with_tokens)
    cost_total = sum(r["estimated_cost_usd"] for r in with_tokens if r.get("estimated_cost_usd") is not None)
    any_cost = any(r.get("estimated_cost_usd") is not None for r in with_tokens)

    def _group(key: str) -> list[dict[str, Any]]:
        groups: dict[str, int] = {}
        for r in with_tokens:
            value = str(r.get(key) or "unknown")
            groups[value] = groups.get(value, 0) + (r.get("tokens_input") or 0) + (r.get("tokens_output") or 0)
        return [{"key": k, "tokens": v} for k, v in groups.items()]

    buckets: dict[str, int] = {}
    for r in with_tokens:
        day = r["started_at"].date().isoformat()
        buckets[day] = buckets.get(day, 0) + (r.get("tokens_input") or 0) + (r.get("tokens_output") or 0)

    return {
        "available": True,
        "input_tokens": input_total,
        "output_tokens": output_total,
        "total_tokens": input_total + output_total,
        "estimated_cost_usd": cost_total if any_cost else None,
        "by_agent": _group("agent_name"),
        "by_model": _group("model_name"),
        "by_project": _group("project_id"),
        "over_time": [{"date": d, "tokens": c} for d, c in sorted(buckets.items())],
    }


# -- Evaluations --------------------------------------------------------------------


_EVAL_DIMENSIONS = [
    "goal_completion",
    "constraint_adherence",
    "tool_usage",
    "decision_consistency",
    "correctness",
    "behavioral_reliability",
]


@router.get("/evaluations")
async def evaluations_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)

    result: dict[str, Any] = {}
    for dimension in _EVAL_DIMENSIONS:
        values: list[tuple[str, float]] = []
        failures: list[dict[str, Any]] = []
        for r in runs:
            risk_assessments = await repository.list_risk_assessments(r["id"])
            root_cause = await repository.get_root_cause(r["id"])
            report = build_reliability_report(r, risk_assessments=risk_assessments, root_cause=root_cause)
            d = report.to_dict()["dimensions"][dimension]
            if d["available"]:
                values.append((r["id"], d["value"]))
                if d["value"] < 0.5:
                    failures.append({"run_id": r["id"], "value": d["value"], "started_at": r["started_at"]})

        current = statistics.mean(v for _rid, v in values) if values else None
        pass_count = sum(1 for _rid, v in values if v >= 0.5)
        fail_count = sum(1 for _rid, v in values if v < 0.5)
        result[dimension] = {
            "current_score": current,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "trend": [{"run_id": rid, "value": v} for rid, v in values[-30:]],
            "recent_failures": sorted(failures, key=lambda f: f["started_at"], reverse=True)[:10],
        }
    return result


# -- Risk / Confidence ----------------------------------------------------------------


@router.get("/risk")
async def risk_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)

    risk_points: list[dict[str, Any]] = []
    confidence_points: list[dict[str, Any]] = []
    high_risk_runs: list[dict[str, Any]] = []
    low_confidence_decisions: list[dict[str, Any]] = []

    for r in runs:
        assessments = await repository.list_risk_assessments(r["id"])
        if not assessments:
            continue
        latest = assessments[-1]
        risk_points.append({"run_id": r["id"], "risk_score": latest["risk_score"], "started_at": r["started_at"]})
        confidence_points.append({"run_id": r["id"], "confidence": latest["confidence"], "started_at": r["started_at"]})
        if latest["risk_score"] >= 0.6:
            high_risk_runs.append({"run_id": r["id"], "risk_score": latest["risk_score"], "status": r["status"]})

        decisions = await repository.list_decisions(r["id"])
        for d in decisions:
            if d.get("confidence") is not None and d["confidence"] < 0.7:
                low_confidence_decisions.append(
                    {"run_id": r["id"], "outcome": d["outcome"], "confidence": d["confidence"], "reason": d["reason"]}
                )

    risk_values = [p["risk_score"] for p in risk_points]
    confidence_values = [p["confidence"] for p in confidence_points]

    return {
        "risk_distribution": _histogram(risk_values),
        "confidence_distribution": _histogram(confidence_values),
        "risk_over_time": sorted(risk_points, key=lambda p: p["started_at"]),
        "high_risk_runs": sorted(high_risk_runs, key=lambda r: r["risk_score"], reverse=True)[:20],
        "low_confidence_decisions": low_confidence_decisions[:20],
    }


def _histogram(values: list[float], buckets: int = 5) -> list[dict[str, Any]]:
    if not values:
        return []
    width = 1.0 / buckets
    counts = [0] * buckets
    for v in values:
        idx = min(buckets - 1, int(v / width))
        counts[idx] += 1
    return [{"range": f"{round(i * width, 2)}-{round((i + 1) * width, 2)}", "count": c} for i, c in enumerate(counts)]


# -- Recovery Center ------------------------------------------------------------------


@router.get("/recovery")
async def recovery_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)

    attempts: list[dict[str, Any]] = []
    for r in runs:
        decisions = await repository.list_decisions(r["id"])
        outcomes = [d["outcome"] for d in decisions]
        # perform_action()/request_approval() (the live in-function human
        # review path) never saves a Decision(outcome="human") row — only
        # the post-hoc low-confidence+high-impact escalation does. Without
        # this, a run reviewed via that live path (e.g. rejected) is
        # invisible here even though a real HumanDecision row exists.
        human_decisions = await repository.list_human_decisions(r["id"])
        if human_decisions and "human" not in outcomes:
            outcomes.append("human")
        if not any(o in ("retry", "replan", "human", "rolled_back") for o in outcomes):
            continue
        root_cause = await repository.get_root_cause(r["id"])
        attempts.append(
            {
                "run_id": r["id"],
                "agent_name": r["agent_name"],
                "status": r["status"],
                "outcomes": outcomes,
                "root_cause": root_cause["explanation"] if root_cause else None,
                "recovered": r["status"] == "continue" and r.get("parent_run_id") is not None,
                "started_at": r["started_at"],
            }
        )

    return {
        "recovery_attempts": len(attempts),
        "successful_recoveries": sum(1 for a in attempts if a["recovered"]),
        "rollbacks": sum(1 for a in attempts if "rolled_back" in a["outcomes"]),
        "retries": sum(1 for a in attempts if "retry" in a["outcomes"]),
        "replans": sum(1 for a in attempts if "replan" in a["outcomes"]),
        "human_interventions": sum(1 for a in attempts if "human" in a["outcomes"]),
        "attempts": sorted(attempts, key=lambda a: a["started_at"], reverse=True)[:50],
    }


# -- Tools ----------------------------------------------------------------------------


@router.get("/tools")
async def tools_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    tool_names = await repository.list_tools(workspace_id=workspace_id)
    engine = ToolProfileEngine(repository)
    profiles = [(await engine.profile(name)).model_dump() for name in tool_names]
    alternatives = await repository.list_tool_alternatives(workspace_id=workspace_id)
    return {"tools": profiles, "alternatives": alternatives}


class ToolAlternativeRequest(BaseModel):
    primary: str
    fallback: str
    reliability_threshold: float = 0.8


@router.post("/tools/alternatives")
async def create_tool_alternative_v2(
    body: ToolAlternativeRequest, workspace_id: str = Depends(get_current_workspace_id)
):
    """The only prior persistence path (legacy POST /api/tools/alternatives,
    server/api.py) never set workspace_id, so it could never be seen by
    this workspace-scoped /tools page — see AGENTGUARD.md's Tier 3 notes."""
    repository = get_repository()
    alt = ToolAlternative(
        primary=body.primary, fallback=body.fallback,
        reliability_threshold=body.reliability_threshold, workspace_id=workspace_id,
    )
    await repository.save_tool_alternative(alt)
    return alt.model_dump()


# -- LLM Gateway (Models) -----------------------------------------------------------------


@router.get("/models")
async def models_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    model_names = await repository.list_llm_models(workspace_id=workspace_id)
    engine = ModelProfileEngine(repository)
    profiles = [(await engine.profile(name, workspace_id=workspace_id)).model_dump() for name in model_names]
    alternatives = await repository.list_model_alternatives(workspace_id=workspace_id)
    return {"models": profiles, "alternatives": alternatives}


# -- Agent Behavior ---------------------------------------------------------------------


@router.get("/agents")
async def list_agents_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    return await repository.list_agent_registrations(workspace_id)


@router.get("/agents/{agent_name}/fingerprint")
async def agent_fingerprint_v2(
    agent_name: str, workspace_id: str = Depends(get_current_workspace_id), current_run_id: str | None = None
):
    repository = get_repository()
    engine = FingerprintEngine(repository)
    fingerprint = await engine.fingerprint(agent_name, current_run_id=current_run_id, workspace_id=workspace_id)
    return fingerprint.model_dump()


@router.get("/agents/{agent_name}/card")
async def agent_card_v2(agent_name: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    agent = await repository.get_agent_registration(workspace_id, agent_name)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")

    recent_runs = await repository.list_runs_by_agent(agent_name, limit=1, workspace_id=workspace_id)
    latest_agent_version = recent_runs[0].get("agent_version") if recent_runs else None

    card = build_agent_card(agent, latest_agent_version=latest_agent_version)
    return card.model_dump(mode="json")


# -- Policies -----------------------------------------------------------------------------


@router.get("/policies")
async def list_policies_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    return await repository.list_policy_definitions(workspace_id=workspace_id)


@router.get("/policies/{name}")
async def get_policy_v2(name: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    definition = await repository.get_policy_definition(name, workspace_id=workspace_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="policy not found")
    current = await repository.get_policy_version(name, definition["current_version"], workspace_id=workspace_id)
    return {**definition, "policy": current["policy"] if current else None}


@router.get("/policies/{name}/versions")
async def get_policy_versions_v2(name: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    return await repository.list_policy_versions(name, workspace_id=workspace_id)


class PolicyCreateRequestV2(BaseModel):
    name: str
    policy: dict
    status: str = "active"


@router.post("/policies")
async def create_policy_v2(body: PolicyCreateRequestV2, workspace_id: str = Depends(get_current_workspace_id)):
    from agentguard.models import Policy

    repository = get_repository()
    registry = PolicyRegistry(repository)
    policy = Policy(**{k: v for k, v in body.policy.items() if k in Policy.model_fields})
    policy_with_ws = policy.model_copy()
    try:
        record = await _create_scoped(registry, body.name, policy_with_ws, workspace_id)
    except PolicyRegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": body.name, "version": record.version, "policy": record.policy.model_dump()}


class PolicyVersionRequestV2(BaseModel):
    policy: dict


@router.post("/policies/{name}/versions")
async def save_policy_version_v2(name: str, body: PolicyVersionRequestV2, workspace_id: str = Depends(get_current_workspace_id)):
    from agentguard.models import Policy

    repository = get_repository()
    registry = PolicyRegistry(repository)
    policy = Policy(**{k: v for k, v in body.policy.items() if k in Policy.model_fields})
    try:
        record = await _save_version_scoped(registry, name, policy, workspace_id)
    except PolicyRegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "version": record.version, "policy": record.policy.model_dump()}


async def _create_scoped(registry: PolicyRegistry, name: str, policy, workspace_id: str):
    """`PolicyRegistry` (Phase 4) is workspace-agnostic by construction
    (its repository calls default `workspace_id=None`); Dashboard V2
    scopes it by constructing the `PolicyDefinition`/`PolicyVersionRecord`
    directly with `workspace_id` set, reusing the registry's version-
    increment logic rather than duplicating it."""
    from agentguard.models import PolicyDefinition, PolicyVersionRecord

    existing = await registry._repository.get_policy_definition(name, workspace_id=workspace_id)
    if existing is not None:
        raise PolicyRegistryError(f"policy {name!r} already exists in this workspace")
    version = 1
    policy_with_version = policy.model_copy(update={"version": version})
    record = PolicyVersionRecord(policy_name=name, workspace_id=workspace_id, version=version, policy=policy_with_version)
    await registry._repository.save_policy_version(record)
    definition = PolicyDefinition(name=name, workspace_id=workspace_id, current_version=version)
    await registry._repository.save_policy_definition(definition)
    return record


async def _save_version_scoped(registry: PolicyRegistry, name: str, policy, workspace_id: str):
    from agentguard.models import PolicyDefinition, PolicyVersionRecord

    definition = await registry._repository.get_policy_definition(name, workspace_id=workspace_id)
    if definition is None:
        raise PolicyRegistryError(f"policy {name!r} does not exist in this workspace")
    new_version = definition["current_version"] + 1
    policy_with_version = policy.model_copy(update={"version": new_version})
    record = PolicyVersionRecord(policy_name=name, workspace_id=workspace_id, version=new_version, policy=policy_with_version)
    await registry._repository.save_policy_version(record)
    updated_definition = PolicyDefinition(
        name=name, workspace_id=workspace_id, current_version=new_version, created_at=definition["created_at"],
        updated_at=datetime.now(timezone.utc),
    )
    await registry._repository.save_policy_definition(updated_definition)
    return record


# -- Improvements -----------------------------------------------------------------------


@router.get("/improvements")
async def list_improvements_v2(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    return await repository.list_improvement_candidates(workspace_id=workspace_id)


# -- Failure Patterns -----------------------------------------------------------------


@router.get("/failure-patterns")
async def failure_patterns_v2(workspace_id: str = Depends(get_current_workspace_id)):
    """Groups this workspace's runs by their RootCause explanation —
    real, already-persisted root causes, never invented pattern labels."""
    repository = get_repository()
    runs = await repository.list_runs(limit=10_000, workspace_id=workspace_id)

    patterns: dict[str, dict[str, Any]] = {}
    for r in runs:
        root_cause = await repository.get_root_cause(r["id"])
        if root_cause is None:
            continue
        key = root_cause["explanation"]
        entry = patterns.setdefault(
            key, {"explanation": key, "earliest_deviation": root_cause["earliest_deviation"], "count": 0, "run_ids": []}
        )
        entry["count"] += 1
        entry["run_ids"].append(r["id"])

    return sorted(patterns.values(), key=lambda p: p["count"], reverse=True)


# -- Problems (cross-run failure clustering) ---------------------------------------------


@router.get("/problems")
async def list_problems_v2(workspace_id: str = Depends(get_current_workspace_id)):
    engine = FailureClusterEngine(get_repository())
    clusters = await engine.cluster(workspace_id=workspace_id)
    return [c.model_dump(mode="json") for c in clusters]


@router.get("/problems/{cluster_key}")
async def get_problem_v2(cluster_key: str, workspace_id: str = Depends(get_current_workspace_id)):
    engine = FailureClusterEngine(get_repository())
    clusters = await engine.cluster(workspace_id=workspace_id)
    match = next((c for c in clusters if c.cluster_key == cluster_key), None)
    if match is None:
        raise HTTPException(status_code=404, detail="problem not found")
    return match.model_dump(mode="json")


class ProblemStatusRequestV2(BaseModel):
    status: str
    triaged_by: str


@router.post("/problems/{cluster_key}/status")
async def set_problem_status_v2(
    cluster_key: str, body: ProblemStatusRequestV2, workspace_id: str = Depends(get_current_workspace_id)
):
    if not body.triaged_by:
        raise HTTPException(status_code=400, detail="triaged_by is required")
    repository = get_repository()
    engine = FailureClusterEngine(repository)
    clusters = await engine.cluster(workspace_id=workspace_id)
    match = next((c for c in clusters if c.cluster_key == cluster_key), None)
    if match is None:
        raise HTTPException(status_code=404, detail="problem not found")
    await repository.save_problem_status(
        workspace_id, cluster_key, body.status,
        linked_candidate_id=match.linked_candidate_id, triaged_by=body.triaged_by,
    )
    return {"cluster_key": cluster_key, "status": body.status}


class ProposeFixRequestV2(BaseModel):
    triaged_by: str


@router.post("/problems/{cluster_key}/propose-fix")
async def propose_fix_v2(
    cluster_key: str, body: ProposeFixRequestV2, workspace_id: str = Depends(get_current_workspace_id)
):
    if not body.triaged_by:
        raise HTTPException(status_code=400, detail="triaged_by is required")
    repository = get_repository()
    engine = FailureClusterEngine(repository)
    clusters = await engine.cluster(workspace_id=workspace_id)
    match = next((c for c in clusters if c.cluster_key == cluster_key), None)
    if match is None:
        raise HTTPException(status_code=404, detail="problem not found")

    try:
        candidate, _ = await propose_improvement(
            repository, match.representative_run_id, corpus_run_ids=match.run_ids
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    candidate.workspace_id = workspace_id
    await repository.save_improvement_candidate(candidate)
    await repository.save_problem_status(
        workspace_id, cluster_key, "fix_proposed",
        linked_candidate_id=candidate.id, triaged_by=body.triaged_by,
    )
    return candidate.model_dump(mode="json")


# -- Evaluation Platform: Evaluation Runs page -------------------------------------------


@router.get("/suites")
async def list_suites_v2(workspace_id: str = Depends(get_current_workspace_id)):
    return await get_repository().list_evaluation_suites(workspace_id=workspace_id)


class SuiteCreateRequestV2(BaseModel):
    name: str
    app_type: str | None = None
    metrics: list[SuiteMetric] = []


@router.post("/suites")
async def create_suite_v2(body: SuiteCreateRequestV2, workspace_id: str = Depends(get_current_workspace_id)):
    """`GET /suites/recommend` below builds a preview suite but never
    persists it — this is the endpoint that closes that gap, so the
    dashboard flow is: recommend (preview) -> review/edit -> POST /suites
    (persist) -> POST /eval-runs (trigger)."""
    suite = EvaluationSuite(name=body.name, workspace_id=workspace_id, app_type=body.app_type, metrics=body.metrics)
    await get_repository().save_evaluation_suite(suite)
    return suite.model_dump(mode="json")


@router.get("/suites/recommend")
async def recommend_suite_v2(agent_name: str, workspace_id: str = Depends(get_current_workspace_id)):
    engine = EvaluationRecommendationEngine(get_repository())
    suite = await engine.recommend(agent_name, workspace_id=workspace_id)
    return suite.model_dump(mode="json")


@router.get("/eval-runs")
async def list_eval_runs_v2(workspace_id: str = Depends(get_current_workspace_id)):
    """Evaluation Platform EvaluationRun rows (§17) — deliberately NOT
    at `/evaluations`, which is already the pre-existing per-Run
    Reliability-dimension endpoint above (`evaluations_v2`) scoring a
    Run against its Policy; this is a different subsystem (see
    models.py's own EvaluationResult docstring on why the two are kept
    as separate classes/endpoints rather than overloading one name)."""
    return await get_repository().list_evaluation_runs(workspace_id=workspace_id)


@router.get("/eval-runs/{evaluation_run_id}")
async def get_eval_run_v2(evaluation_run_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    evaluation_run = await repository.get_evaluation_run(evaluation_run_id)
    if evaluation_run is None or evaluation_run.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    results = await repository.list_evaluation_results(evaluation_run_id)
    return {"evaluation_run": evaluation_run, "results": results}


class EvalRunCreateRequestV2(BaseModel):
    suite_id: str
    source_run_ids: list[str]


@router.post("/eval-runs", status_code=202)
async def create_eval_run_v2(body: EvalRunCreateRequestV2, workspace_id: str = Depends(get_current_workspace_id)):
    """Enqueues an evaluation_suite_run job rather than executing
    synchronously (a suite run can take minutes over many cases/metrics).
    Every source_run_id's ownership is checked here, before enqueueing —
    isolation enforced at the API boundary, not inside the job handler —
    so a caller gets an immediate 404 instead of a job that silently
    skips runs it can't see. Returns just a job_id: EvaluationRunStatus
    has no "queued" value yet and EvaluationEngine.run_suite() only
    creates the EvaluationRun row once cases are being evaluated, so the
    dashboard polls GET /jobs/{job_id} first and switches to the existing
    GET /eval-runs/{id} once the run appears."""
    repository = get_repository()
    suite = await repository.get_evaluation_suite(body.suite_id)
    if suite is None or suite.get("workspace_id") not in (workspace_id, None):
        raise HTTPException(status_code=404, detail="evaluation suite not found")
    for run_id in body.source_run_ids:
        await _owned_run(workspace_id, run_id)
    job = Job(
        kind=EVALUATION_SUITE_RUN_JOB_KIND,
        payload={"suite_id": body.suite_id, "source_run_ids": body.source_run_ids, "workspace_id": workspace_id},
        workspace_id=workspace_id,
    )
    await repository.enqueue_job(job)
    return {"job_id": job.id, "status": "queued"}


@router.get("/jobs/{job_id}")
async def get_job_v2(job_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    job = await get_repository().get_job(job_id)
    if job is None or job.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/eval-results/{result_id}/diagnose")
async def diagnose_eval_result_v2(result_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    """WHY a metric failed, WHERE in the traced code, and WHAT prompt
    might fix it (agentguard/evaluation/diagnose.py) — never fabricates
    a suggestion when no real judge/provider is configured; see that
    module's own EvaluationDiagnosis.available/.unavailable_reason."""
    repository = get_repository()
    result = await repository.get_evaluation_result(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation result not found")
    evaluation_run = await repository.get_evaluation_run(result["evaluation_run_id"])
    if evaluation_run is None or evaluation_run.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="evaluation result not found")
    diagnosis = await diagnose_evaluation_failure(repository, result_id)
    return diagnosis.__dict__


# -- Evaluation Platform: Dataset Quality page --------------------------------------------


@router.get("/datasets")
async def list_datasets_v2(workspace_id: str = Depends(get_current_workspace_id)):
    return await get_repository().list_datasets(workspace_id=workspace_id)


async def _owned_dataset(workspace_id: str, dataset_id: str) -> dict[str, Any]:
    repository = get_repository()
    dataset = await repository.get_dataset(dataset_id)
    if dataset is None or dataset.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


@router.get("/datasets/{dataset_id}/versions")
async def list_dataset_versions_v2(dataset_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    await _owned_dataset(workspace_id, dataset_id)
    return await get_repository().list_dataset_versions(dataset_id)


@router.get("/datasets/{dataset_id}/versions/{version}")
async def get_dataset_version_v2(dataset_id: str, version: int, workspace_id: str = Depends(get_current_workspace_id)):
    await _owned_dataset(workspace_id, dataset_id)
    repository = get_repository()
    dataset_version = await repository.get_dataset_version(dataset_id, version)
    if dataset_version is None:
        raise HTTPException(status_code=404, detail="dataset version not found")
    examples = await repository.list_dataset_examples(dataset_version["id"])
    for example in examples:
        example["evidence"] = await repository.list_evidence_for_example(example["id"])
    status_counts: dict[str, int] = {}
    for example in examples:
        status_counts[example["golden_status"]] = status_counts.get(example["golden_status"], 0) + 1
    return {"dataset_version": dataset_version, "examples": examples, "status_counts": status_counts}


# -- Evaluation Platform: Model Benchmarks page ----------------------------------


@router.get("/benchmarks")
async def list_benchmarks_v2(workspace_id: str = Depends(get_current_workspace_id)):
    return await get_repository().list_model_benchmarks(workspace_id=workspace_id)


@router.get("/benchmarks/{benchmark_id}")
async def get_benchmark_v2(benchmark_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    benchmark = await repository.get_model_benchmark(benchmark_id)
    if benchmark is None or benchmark.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="benchmark not found")
    rows = await repository.list_model_benchmark_results(benchmark_id)
    results = []
    for row in rows:
        eval_result = await repository.get_evaluation_result(row["evaluation_result_id"])
        results.append({**row, "metric": eval_result.get("metric") if eval_result else None,
                        "score": eval_result.get("score") if eval_result else None,
                        "available": eval_result.get("available") if eval_result else False})
    return {"benchmark": benchmark, "results": results}


# -- Evaluation Platform: Recommendations page -----------------------------------


@router.get("/eval-recommendations")
async def list_eval_recommendations_v2(
    kind: str | None = None, workspace_id: str = Depends(get_current_workspace_id)
):
    """Distinct from the pre-existing `/improvements` (auto-fix
    proposals) and `/failure-patterns` endpoints — these are
    EvaluationRecommendationEngine/ModelBenchmarkEngine's stored
    Recommendation rows (metric-suite or model recommendations,
    design doc §10/§11), never at the plain `/recommendations` path,
    which is already the Improvement-candidate page above."""
    return await get_repository().list_recommendations(kind=kind, workspace_id=workspace_id)


# -- Skills Generator -----------------------------------------------------------------


@router.get("/skills/options")
async def get_skill_options_v2(workspace_id: str = Depends(get_current_workspace_id)):
    return list_skill_options()


class SkillGenerateRequestV2(BaseModel):
    project_id: str
    framework: str
    categories: list[str] = []
    metrics: dict[str, list[str]] = {}
    judge_model: str = "gpt-4o-mini"


@router.post("/skills/generate")
async def generate_skill_v2(body: SkillGenerateRequestV2, workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    project = await repository.get_project(body.project_id)
    if project is None or project.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="project not found")

    request = SkillRequest(
        project_id=project["id"],
        project_name=project["name"],
        api_base_url="http://127.0.0.1:8000",
        framework=body.framework,
        selected_categories=tuple(body.categories),
        selected_metrics={k: tuple(v) for k, v in body.metrics.items()},
        judge_model=body.judge_model,
    )
    try:
        markdown = compose_skill(request)
    except (UnknownFrameworkError, UnknownCategoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"markdown": markdown}
