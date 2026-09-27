"""Auto-improvement orchestration — Phase 4.

    flagged run -> root cause -> recommendation -> optimizer -> candidate
    -> historical regression evaluation -> comparison -> human approval

Nothing here ever deploys a candidate. `propose_improvement()` only ever
leaves a candidate in status "proposed"; `approve_candidate()`/
`reject_candidate()` are the only state transitions, and both require an
explicit caller-supplied `approved_by` — there is no code path that
flips status without one (Rule: "A human/developer must approve
deployment").
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import ImprovementCandidate, ImprovementEvaluation
from ..regression.corpus import AgentCallable, RegressionCorpus
from .optimizer import PromptOptimizer, get_default_optimizer
from .recommend import generate_recommendation


class ApprovalError(ValueError):
    pass


async def propose_improvement(
    repository: Any,
    run_id: str,
    corpus_run_ids: list[str],
    *,
    candidate_agent_fn: AgentCallable | None = None,
    optimizer: PromptOptimizer | None = None,
) -> tuple[ImprovementCandidate, ImprovementEvaluation | None]:
    """1. Root-cause the flagged run. 2. Generate a deterministic
    recommendation. 3. Run it through a PromptOptimizer (real DSPy or
    the deterministic stand-in — see optimizer.py) to get a candidate.
    4. Persist the candidate as PROPOSED. 5. If `candidate_agent_fn` is
    supplied (a real, runnable implementation of the candidate change),
    replay the historical corpus against it and persist a factual
    ImprovementEvaluation comparing baseline vs. candidate. Without one,
    the candidate is still proposed and persisted, but no evaluation is
    fabricated — `None` is returned for it instead."""
    root_cause = await repository.get_root_cause(run_id)
    recommendation = generate_recommendation(root_cause)

    resolved_optimizer = optimizer or get_default_optimizer()
    candidate = await resolved_optimizer.optimize(run_id, recommendation)
    await repository.save_improvement_candidate(candidate)

    if candidate_agent_fn is None:
        return candidate, None

    corpus = await RegressionCorpus.from_run_ids(repository, corpus_run_ids)
    corpus_evaluation = await corpus.evaluate_candidate(repository, candidate_agent_fn)

    evaluation = ImprovementEvaluation(
        candidate_id=candidate.id,
        baseline_run_ids=corpus_evaluation.baseline_run_ids,
        candidate_run_ids=corpus_evaluation.candidate_run_ids,
        comparison=corpus_evaluation.comparison.to_dict(),
    )
    await repository.save_improvement_evaluation(evaluation)
    return candidate, evaluation


async def approve_candidate(repository: Any, candidate_id: str, approved_by: str) -> ImprovementCandidate:
    if not approved_by:
        raise ApprovalError("approve_candidate requires an explicit approved_by")
    data = await repository.get_improvement_candidate(candidate_id)
    if data is None:
        raise ApprovalError(f"improvement candidate {candidate_id!r} not found")
    candidate = ImprovementCandidate(**data)
    if candidate.status != "proposed":
        raise ApprovalError(f"candidate {candidate_id!r} is not in 'proposed' status (status={candidate.status!r})")
    candidate.status = "approved"
    candidate.approved_by = approved_by
    candidate.resolved_at = datetime.now(timezone.utc)
    await repository.save_improvement_candidate(candidate)
    return candidate


async def reject_candidate(repository: Any, candidate_id: str, rejected_by: str) -> ImprovementCandidate:
    if not rejected_by:
        raise ApprovalError("reject_candidate requires an explicit rejected_by")
    data = await repository.get_improvement_candidate(candidate_id)
    if data is None:
        raise ApprovalError(f"improvement candidate {candidate_id!r} not found")
    candidate = ImprovementCandidate(**data)
    if candidate.status != "proposed":
        raise ApprovalError(f"candidate {candidate_id!r} is not in 'proposed' status (status={candidate.status!r})")
    candidate.status = "rejected"
    candidate.approved_by = rejected_by
    candidate.resolved_at = datetime.now(timezone.utc)
    await repository.save_improvement_candidate(candidate)
    return candidate
