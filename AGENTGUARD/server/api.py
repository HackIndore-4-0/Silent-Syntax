"""AgentGuard dashboard API — read-only endpoints over the same
RunRepository the SDK writes through, plus the static dashboard page.

Phase 1 scope: run list + run detail.

Phase 2 adds:
    GET  /runs/{run_id}/risk
    GET  /runs/{run_id}/root-cause
    GET  /runs/{run_id}/decisions
    POST /runs/{run_id}/human-decision
    WS   /ws/runs/{run_id}/approval

No trace visualization, full reliability report, or replay UI yet —
those are later phases.

Run with:  uvicorn server.api:app --reload
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agentguard._runtime import get_repository
from agentguard.audit.chain import verify_audit_chain
from agentguard.ci.gate import CIGateError
from agentguard.human.broker import get_broker
from agentguard.human.resolution import resolve_human_review
from agentguard.improve import ApprovalError, approve_candidate, reject_candidate
from agentguard.models import Policy, ToolAlternative
from agentguard.policy.registry import PolicyRegistry, PolicyRegistryError
from agentguard.recovery import RollbackError, rollback
from agentguard.regression.compare import compare_runs
from agentguard.reliability.fingerprint import FingerprintEngine
from agentguard.reliability.report import build_reliability_report
from agentguard.reliability.tool_profile import ToolProfileEngine
from agentguard.replay import replay as replay_run

logger = logging.getLogger("agentguard.server")

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

app = FastAPI(title="AgentGuard Dashboard API")

# Dashboard V2: authentication, API keys, projects/team (server/auth.py)
# and the authenticated, workspace-scoped data API the new dashboard UI
# consumes (server/dashboard_v2.py). Every endpoint below this point in
# the file (the original, unauthenticated `/api/...` surface) is
# UNCHANGED — still used by the CLI, examples, and the existing test
# suite exactly as before (Rule: "Preserve all existing AgentGuard
# backend functionality and tests").
from . import auth as _auth_module  # noqa: E402
from . import dashboard_v2 as _dashboard_v2_module  # noqa: E402

app.include_router(_auth_module.router)
app.include_router(_dashboard_v2_module.router)


@app.get("/api/runs")
async def list_runs(limit: int = 50, offset: int = 0):
    repository = get_repository()
    return await repository.list_runs(limit=limit, offset=offset)


@app.get("/api/runs/compare")
async def get_runs_compare(run_a: str, run_b: str):
    """Registered BEFORE /api/runs/{run_id} so "compare" is never
    swallowed as a run_id — FastAPI/Starlette match routes in
    registration order."""
    repository = get_repository()
    try:
        result = await compare_runs(repository, run_a, run_b)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result.to_dict()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/api/runs/{run_id}/risk")
async def get_risk(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await repository.list_risk_assessments(run_id)


@app.get("/api/runs/{run_id}/root-cause")
async def get_root_cause(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    root_cause = await repository.get_root_cause(run_id)
    if root_cause is None:
        return {"run_id": run_id, "root_cause": None}
    return root_cause


@app.get("/api/runs/{run_id}/decisions")
async def get_decisions(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await repository.list_decisions(run_id)


@app.get("/api/runs/{run_id}/human-decisions")
async def get_human_decisions(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await repository.list_human_decisions(run_id)


@app.get("/api/runs/{run_id}/timeline")
async def get_timeline(run_id: str):
    """Chronological audit-trail timeline for a run — RUN START, each
    AGENT STEP, POLICY CHECK, EVALUATION, RISK ASSESSMENT, ROOT CAUSE,
    DECISION, CHECKPOINT, HUMAN, ROLLBACK, RUN COMPLETION, in the exact
    order they happened. Built directly from the same hash-chained
    AuditEvent rows `/audit` and `/audit/verify` use — the timeline is
    a rendering of the audit trail, not a separate record."""
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await repository.list_audit_events(run_id)
    return {
        "run_id": run_id,
        "events": [
            {
                "seq": e["seq"],
                "event_type": e["event_type"],
                "created_at": e["created_at"],
                "policy_version": e.get("policy_version"),
                "payload": e["payload"],
            }
            for e in events
        ],
    }


@app.get("/api/runs/{run_id}/reliability-report")
async def get_reliability_report(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    risk_assessments = await repository.list_risk_assessments(run_id)
    root_cause = await repository.get_root_cause(run_id)
    audit_events = await repository.list_audit_events(run_id)
    report = build_reliability_report(
        run, risk_assessments=risk_assessments, root_cause=root_cause, audit_events=audit_events
    )
    return report.to_dict()


@app.get("/api/runs/{run_id}/checkpoints")
async def get_checkpoints(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await repository.list_checkpoints(run_id)


class RollbackRequest(BaseModel):
    to_checkpoint: str
    """A Checkpoint.id or its label (e.g. "S2")."""


@app.post("/api/runs/{run_id}/rollback")
async def post_rollback(run_id: str, body: RollbackRequest):
    """Restore `run_id`'s state to `to_checkpoint`. Synchronous — this is
    an explicit, separately-invoked recovery operation, not part of any
    agent's own response path (see the Phase 3 spec's Latency section)."""
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        result = await rollback(repository, run_id, body.to_checkpoint)
    except RollbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "run_id": run_id,
        "checkpoint": result.checkpoint.model_dump(mode="json"),
        "previous_state": result.previous_state,
        "restored_state": result.restored_state,
        "decision": result.decision.model_dump(mode="json"),
    }


@app.get("/api/runs/{run_id}/counterfactual")
async def get_counterfactual(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    result = await repository.get_counterfactual(run_id)
    if result is None:
        return {"run_id": run_id, "counterfactual": None, "detail": "no counterfactual analysis recorded for this run yet"}
    return result


@app.get("/api/runs/{run_id}/audit")
async def get_audit(run_id: str):
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await repository.list_audit_events(run_id)
    return {"run_id": run_id, "event_count": len(events), "events": events}


@app.post("/api/runs/{run_id}/audit/verify")
async def post_audit_verify(run_id: str):
    """Recomputes the run's SHA-256 hash chain and reports whether it is
    intact — see agentguard/audit/chain.py:verify_audit_chain."""
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await verify_audit_chain(repository, run_id)


class HumanDecisionRequest(BaseModel):
    outcome: Literal["approved", "rejected", "replan"]
    request_id: str | None = None
    resolved_by: str | None = None
    reason: str = ""


@app.post("/api/runs/{run_id}/human-decision")
async def post_human_decision(run_id: str, body: HumanDecisionRequest):
    """Resolves a pending HUMAN decision — APPROVE / REJECT / REQUEST REPLAN.

    Works whether the pending request is a live in-function wait (a
    `perform_action`/`request_approval` call currently suspended) or a
    post-hoc "low confidence + high impact" escalation with nothing
    blocked in `await`; see agentguard/human/resolution.py.
    """
    repository = get_repository()
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    result = await resolve_human_review(
        repository,
        run_id,
        body.outcome,
        request_id=body.request_id,
        resolved_by=body.resolved_by,
        reason=body.reason,
    )
    if not result.resolved:
        return {"run_id": run_id, "resolved": False, "detail": "no matching pending human decision"}
    return {
        "run_id": run_id,
        "resolved": True,
        "live": result.live,
        "decision": result.decision.model_dump() if result.decision else None,
    }


@app.get("/api/runs/{run_id}/replay")
async def get_replay(run_id: str):
    """SAFE/DRY-RUN reconstruction only — reads persisted data, never
    re-executes agent code or a registered tool. See agentguard/replay/engine.py."""
    repository = get_repository()
    try:
        session = await replay_run(repository, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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


@app.get("/api/tools")
async def list_tools():
    repository = get_repository()
    return await repository.list_tools()


@app.get("/api/tools/{tool_name}/profile")
async def get_tool_profile(tool_name: str):
    repository = get_repository()
    profile = await ToolProfileEngine(repository).profile(tool_name)
    return profile.model_dump()


@app.get("/api/tools/alternatives")
async def list_tool_alternatives():
    repository = get_repository()
    return await repository.list_tool_alternatives()


class ToolAlternativeRequest(BaseModel):
    primary: str
    fallback: str
    reliability_threshold: float = 0.8


@app.post("/api/tools/alternatives")
async def post_tool_alternative(body: ToolAlternativeRequest):
    """Explicitly registers a primary -> fallback tool mapping (Control
    Portal's Tool Recovery section). Persists it AND updates the
    in-process ToolRegistry that agentguard.call_tool() actually reads
    from, so a portal-registered alternative takes effect immediately."""
    from agentguard.tools.registry import get_registry

    repository = get_repository()
    alternative = ToolAlternative(
        primary=body.primary, fallback=body.fallback, reliability_threshold=body.reliability_threshold
    )
    await repository.save_tool_alternative(alternative)
    get_registry().register_alternative(
        body.primary, body.fallback, reliability_threshold=body.reliability_threshold
    )
    return alternative.model_dump()


@app.get("/api/agents/{agent_id}/fingerprint")
async def get_fingerprint(agent_id: str, current_run_id: str | None = None):
    repository = get_repository()
    fingerprint = await FingerprintEngine(repository).fingerprint(agent_id, current_run_id=current_run_id)
    return fingerprint.model_dump()


@app.get("/api/improvements")
async def list_improvements():
    repository = get_repository()
    return await repository.list_improvement_candidates()


@app.get("/api/improvements/{candidate_id}")
async def get_improvement(candidate_id: str):
    repository = get_repository()
    candidate = await repository.get_improvement_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="improvement candidate not found")
    evaluations = await repository.list_improvement_evaluations(candidate_id)
    return {**candidate, "evaluations": evaluations}


class ImprovementApprovalRequest(BaseModel):
    approved_by: str


@app.post("/api/improvements/{candidate_id}/approve")
async def post_improvement_approve(candidate_id: str, body: ImprovementApprovalRequest):
    repository = get_repository()
    try:
        candidate = await approve_candidate(repository, candidate_id, body.approved_by)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return candidate.model_dump(mode="json")


@app.post("/api/improvements/{candidate_id}/reject")
async def post_improvement_reject(candidate_id: str, body: ImprovementApprovalRequest):
    repository = get_repository()
    try:
        candidate = await reject_candidate(repository, candidate_id, body.approved_by)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return candidate.model_dump(mode="json")


@app.get("/api/policies")
async def list_policies():
    repository = get_repository()
    return await repository.list_policy_definitions()


@app.get("/api/policies/{name}")
async def get_policy(name: str):
    repository = get_repository()
    definition = await repository.get_policy_definition(name)
    if definition is None:
        raise HTTPException(status_code=404, detail="policy not found")
    current = await repository.get_policy_version(name, definition["current_version"])
    return {**definition, "policy": current["policy"] if current else None}


@app.get("/api/policies/{name}/versions")
async def get_policy_versions(name: str):
    repository = get_repository()
    return await repository.list_policy_versions(name)


class PolicyCreateRequest(BaseModel):
    name: str
    policy: dict


@app.post("/api/policies")
async def post_policy(body: PolicyCreateRequest):
    """Creates a NEW named policy at version 1. Use POST
    /api/policies/{name}/versions to save a later version — this never
    mutates an existing one."""
    repository = get_repository()
    registry = PolicyRegistry(repository)
    policy = Policy(**{k: v for k, v in body.policy.items() if k in Policy.model_fields})
    try:
        record = await registry.create(body.name, policy)
    except PolicyRegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": body.name, "version": record.version, "policy": record.policy.model_dump()}


class PolicyVersionRequest(BaseModel):
    policy: dict


@app.post("/api/policies/{name}/versions")
async def post_policy_version(name: str, body: PolicyVersionRequest):
    """Saves a NEW version of an existing named policy. Historical runs
    that already executed under a prior version keep referencing it —
    this endpoint only ever inserts a new PolicyVersionRecord."""
    repository = get_repository()
    registry = PolicyRegistry(repository)
    policy = Policy(**{k: v for k, v in body.policy.items() if k in Policy.model_fields})
    try:
        record = await registry.save_new_version(name, policy)
    except PolicyRegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "version": record.version, "policy": record.policy.model_dump()}


class CIGateRequest(BaseModel):
    baseline_run_ids: list[str]
    candidate_run_ids: list[str]
    threshold_pct: float = 5.0
    protected_metrics: list[str] | None = None


@app.post("/api/ci-gate")
async def post_ci_gate(body: CIGateRequest):
    from agentguard.ci.gate import run_ci_gate

    repository = get_repository()
    try:
        result = await run_ci_gate(
            repository,
            body.baseline_run_ids,
            body.candidate_run_ids,
            threshold_pct=body.threshold_pct,
            protected_metrics=body.protected_metrics,
        )
    except CIGateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result.model_dump(mode="json")


@app.websocket("/ws/runs/{run_id}/approval")
async def ws_approval(websocket: WebSocket, run_id: str) -> None:
    """Pushes every pending/resolved approval event for `run_id` to the
    connected dashboard, and accepts the dashboard's decision back over
    the same socket (as an alternative to the REST endpoint above).

    Inbound message shape: {"outcome": "approved"|"rejected"|"replan",
    "request_id": <optional>, "resolved_by": <optional>}.
    """
    await websocket.accept()
    broker = get_broker()
    queue = broker.subscribe(run_id)
    repository = get_repository()

    # Replay currently-pending requests so a dashboard that connects
    # *after* the request was published still sees it.
    for pending in broker.get_pending(run_id):
        await websocket.send_json({"type": "approval_request", "request": pending.model_dump(mode="json")})

    async def _forward() -> None:
        while True:
            message = await queue.get()
            await websocket.send_json(message)

    forward_task = asyncio.create_task(_forward())
    try:
        while True:
            data = await websocket.receive_json()
            outcome = data.get("outcome")
            if outcome not in ("approved", "rejected", "replan"):
                await websocket.send_json({"type": "error", "detail": f"invalid outcome {outcome!r}"})
                continue
            result = await resolve_human_review(
                repository,
                run_id,
                outcome,
                request_id=data.get("request_id"),
                resolved_by=data.get("resolved_by"),
                reason=data.get("reason", ""),
            )
            await websocket.send_json(
                {
                    "type": "human_decision_ack",
                    "resolved": result.resolved,
                    "live": result.live,
                    "decision": result.decision.model_dump(mode="json") if result.decision else None,
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        broker.unsubscribe(run_id, queue)


@app.get("/")
async def dashboard_index():
    return FileResponse(DASHBOARD_DIR / "index.html")


if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR)), name="dashboard")
