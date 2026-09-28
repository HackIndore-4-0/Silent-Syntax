"""Accept/reject a stored model Recommendation — mirrors
agentguard/improve/workflow.py's approve_candidate()/reject_candidate()
exactly: an explicit, non-empty actor is required for every decision,
and a recommendation can only be decided once (status must still be
"pending"), so there is no code path that silently re-decides or
undoes a prior human decision.

Never applies the recommended model anywhere — accepting a
recommendation only records that a human validated it; the spec's own
principle ("never change production automatically") means "apply" is a
separate, explicit, out-of-scope-for-this-module action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class RecommendationDecisionError(ValueError):
    pass


async def accept_recommendation(repository: Any, recommendation_id: str, accepted_by: str) -> dict[str, Any]:
    return await _decide(repository, recommendation_id, "accepted", accepted_by)


async def reject_recommendation(repository: Any, recommendation_id: str, rejected_by: str) -> dict[str, Any]:
    return await _decide(repository, recommendation_id, "rejected", rejected_by)


async def _decide(repository: Any, recommendation_id: str, status: str, decided_by: str) -> dict[str, Any]:
    if not decided_by:
        raise RecommendationDecisionError(f"deciding a recommendation requires an explicit decided_by (status={status!r})")
    data = await repository.get_recommendation(recommendation_id)
    if data is None:
        raise RecommendationDecisionError(f"recommendation {recommendation_id!r} not found")
    if data.get("status", "pending") != "pending":
        raise RecommendationDecisionError(
            f"recommendation {recommendation_id!r} is not 'pending' (status={data.get('status')!r})"
        )
    updated = await repository.update_recommendation_status(
        recommendation_id, status, decided_by, datetime.now(timezone.utc)
    )
    if updated is None:
        raise RecommendationDecisionError(f"recommendation {recommendation_id!r} not found")
    return updated
