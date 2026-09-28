"""accept_recommendation/reject_recommendation (agentguard/evaluation/
recommendation_workflow.py) — mirrors test_improvement.py's coverage of
approve_candidate/reject_candidate: an explicit actor is required, a
recommendation can only be decided once, and deciding it never applies
the recommended model anywhere (that's a deliberately separate, out of
scope, explicit action).
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.evaluation.recommendation_workflow import (
    RecommendationDecisionError,
    accept_recommendation,
    reject_recommendation,
)
from agentguard.models import Recommendation

from ..fakes import InMemoryRunRepository


@pytest.fixture
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _seed_recommendation(fake_repository) -> Recommendation:
    rec = Recommendation(kind="model", subject_id="benchmark-1", reasoning="stub", recommendation={"model": "gpt-4o-mini"})
    await fake_repository.save_recommendation(rec)
    return rec


async def test_accept_requires_explicit_decider(fake_repository):
    rec = await _seed_recommendation(fake_repository)

    with pytest.raises(RecommendationDecisionError):
        await accept_recommendation(fake_repository, rec.id, "")

    accepted = await accept_recommendation(fake_repository, rec.id, "dev@example.com")
    assert accepted["status"] == "accepted"
    assert accepted["decided_by"] == "dev@example.com"
    assert accepted["decided_at"] is not None


async def test_reject_requires_explicit_decider(fake_repository):
    rec = await _seed_recommendation(fake_repository)

    with pytest.raises(RecommendationDecisionError):
        await reject_recommendation(fake_repository, rec.id, "")

    rejected = await reject_recommendation(fake_repository, rec.id, "dev@example.com")
    assert rejected["status"] == "rejected"


async def test_cannot_decide_a_recommendation_twice(fake_repository):
    rec = await _seed_recommendation(fake_repository)

    await accept_recommendation(fake_repository, rec.id, "dev@example.com")

    with pytest.raises(RecommendationDecisionError):
        await accept_recommendation(fake_repository, rec.id, "someone-else@example.com")

    with pytest.raises(RecommendationDecisionError):
        await reject_recommendation(fake_repository, rec.id, "someone-else@example.com")


async def test_deciding_an_unknown_recommendation_raises(fake_repository):
    with pytest.raises(RecommendationDecisionError):
        await accept_recommendation(fake_repository, "does-not-exist", "dev@example.com")


async def test_new_recommendations_default_to_pending(fake_repository):
    rec = await _seed_recommendation(fake_repository)

    stored = await fake_repository.get_recommendation(rec.id)

    assert stored["status"] == "pending"
    assert stored["decided_by"] is None
    assert stored["decided_at"] is None
