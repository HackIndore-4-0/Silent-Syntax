"""Resolves a pending HUMAN decision.

Two independent paths converge here, both real, neither a stub:

1. A LIVE in-function wait: some agent coroutine is currently suspended
   inside `context.request_approval()`, awaiting broker resolution
   (Scenario C — a `perform_action("payment", ...)` call mid-run).
   Resolving here just unblocks that future; the run's eventual status
   (CONTINUE/STOP/REPLAN) is decided by `_execute()`'s own control flow
   once the agent function resumes or raises. See decorator.py.

2. A POST-HOC pending review: the Decision Engine already returned
   HUMAN after the agent function finished executing (the "low
   confidence + high impact" auto-escalation), and the run's *final*
   status is still open — nothing is blocked in `await`. Resolving here
   updates run.status directly, using a fresh DecisionEngine seeded at
   HUMAN (matching the run's actual last Decision Engine state).

Used by `POST /runs/{run_id}/human-decision` (server/api.py) and by the
WebSocket endpoint when a dashboard sends its decision over the socket
instead of a separate REST call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..decision.engine import DecisionEngine
from ..models import Decision, HumanDecision, Policy, Run, RunStatus
from ..storage.repository import RunRepository
from .broker import get_broker


def _run_from_dict(data: dict) -> Run:
    policy_data = data.get("policy") or {}
    policy = Policy(**{k: v for k, v in policy_data.items() if k in Policy.model_fields})
    run_fields = {k: v for k, v in data.items() if k in Run.model_fields and k != "policy"}
    return Run(policy=policy, **run_fields)


@dataclass
class HumanResolutionResult:
    resolved: bool
    """True the moment a matching pending request was found and handed
    an outcome — regardless of which of the two paths below handled it."""
    live: bool
    """True if a coroutine was actually unblocked by this call (the
    in-function `perform_action`/`request_approval` case). When True,
    `decision` is None here: the run's own Decision is recorded later,
    by decorator.py, once that coroutine resumes or raises."""
    decision: Optional[Decision]
    """Set only for the post-hoc path (nothing was blocked in `await`) —
    the Decision this call synthesized and persisted directly."""


async def resolve_human_review(
    repository: RunRepository,
    run_id: str,
    outcome: str,
    *,
    request_id: Optional[str] = None,
    resolved_by: Optional[str] = None,
    reason: str = "",
    modified_evidence: Optional[dict] = None,
) -> HumanResolutionResult:
    """outcome is one of "approved" | "rejected" | "replan". `modified_evidence`
    lets a reviewer approve with corrected params instead of the original
    proposal — see agentguard.context.perform_action_with_result()."""
    pending = await repository.list_human_decisions(run_id)
    open_requests = [p for p in pending if p["status"] == "pending"]
    if request_id is not None:
        open_requests = [p for p in open_requests if p["id"] == request_id]
    if not open_requests:
        return HumanResolutionResult(resolved=False, live=False, decision=None)
    target = open_requests[-1]

    live = get_broker().resolve(target["id"], outcome, resolved_by, modified_evidence, reason=reason or None)
    if live is not None:
        # context.request_approval() will persist the resolved
        # HumanDecision and drive the run's own status transition once
        # the waiting coroutine wakes up.
        return HumanResolutionResult(resolved=True, live=True, decision=None)

    # No live waiter: the post-hoc HUMAN decision. Resolve directly.
    run_data = await repository.get_run(run_id)
    if run_data is None or run_data.get("status") != RunStatus.HUMAN.value:
        return HumanResolutionResult(resolved=False, live=False, decision=None)

    updated = HumanDecision(
        **{
            **{k: v for k, v in target.items() if k in HumanDecision.model_fields},
            "status": outcome, "resolved_by": resolved_by, "modified_evidence": modified_evidence,
        }
    )
    from datetime import datetime, timezone

    updated.resolved_at = datetime.now(timezone.utc)
    await repository.save_human_decision(updated)

    engine = DecisionEngine()
    engine.state = RunStatus.HUMAN
    decision = engine.resolve_human(
        outcome, reason=reason or f"resolved via human-decision API: {outcome}", evidence={"human_decision_id": target["id"]}
    )

    run = _run_from_dict(run_data)
    run.status = decision.outcome
    await repository.set_run_status(run_id, decision.outcome)
    await repository.save_decision(run, decision)
    return HumanResolutionResult(resolved=True, live=False, decision=decision)
