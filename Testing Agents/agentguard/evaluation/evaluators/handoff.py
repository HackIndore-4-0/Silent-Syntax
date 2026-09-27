"""HandoffEvaluator / ChainHandoffEvaluator — Phase 10 (multi-agent
evaluation): scores a handoff by comparing what a parent run's
`final_state` handed off against what the child run's `initial_state`
actually contains.

Reuses `Run.parent_run_id` — the exact same handoff link
`agentguard/recovery/` already uses — no new instrumentation. Fully
deterministic (no judge call): "lost" and "altered" keys are real,
checkable facts about persisted Run rows, never a subjective call.

`HandoffEvaluator` scores exactly one hop (child vs. its immediate
parent). `ChainHandoffEvaluator` walks the FULL chain back to the root
run, scoring every hop and reporting the WEAKEST hop as the overall
score — a multi-agent pipeline is only as trustworthy as its worst
handoff, not its average one.
"""
from __future__ import annotations

from typing import Any

from ...models import EvaluationResult
from ...storage.repository import RunRepository
from .base import EvalCase, MetricEvaluator

_LOST_KEY_WEIGHT = 0.2
_ALTERED_KEY_WEIGHT = 0.1


def _compute_handoff(parent_state: dict[str, Any], child_state: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    lost = sorted(k for k in parent_state if k not in child_state)
    altered = sorted(k for k in parent_state if k in child_state and child_state[k] != parent_state[k])
    score = max(0.0, 1.0 - _LOST_KEY_WEIGHT * len(lost) - _ALTERED_KEY_WEIGHT * len(altered))
    return score, lost, altered


def _describe(lost: list[str], altered: list[str]) -> str:
    parts = []
    if lost:
        parts.append(f"lost keys: {lost}")
    if altered:
        parts.append(f"altered keys: {altered}")
    return "; ".join(parts) or "handoff state preserved exactly"


class HandoffEvaluator(MetricEvaluator):
    name = "handoff"

    def __init__(self, repository: RunRepository) -> None:
        self._repository = repository

    async def evaluate(self, case: EvalCase) -> EvaluationResult:
        child_run_id = case.source_run_id
        if not child_run_id:
            return EvaluationResult(
                evaluation_run_id="",
                metric=self.name,
                available=False,
                reason="no source_run_id on this EvalCase — HandoffEvaluator needs a real child run",
            )

        child = await self._repository.get_run(child_run_id)
        if child is None or not child.get("parent_run_id"):
            return EvaluationResult(
                evaluation_run_id="",
                source_run_id=child_run_id,
                metric=self.name,
                available=False,
                reason="this run has no parent_run_id — not a handoff, nothing to score",
            )

        parent = await self._repository.get_run(child["parent_run_id"])
        if parent is None:
            return EvaluationResult(
                evaluation_run_id="",
                source_run_id=child_run_id,
                metric=self.name,
                available=False,
                reason="parent run not found",
            )

        score, lost, altered = _compute_handoff(parent.get("final_state") or {}, child.get("initial_state") or {})

        return EvaluationResult(
            evaluation_run_id="",
            source_run_id=child_run_id,
            metric=self.name,
            score=score,
            available=True,
            reason=_describe(lost, altered),
        )


class ChainHandoffEvaluator(MetricEvaluator):
    """Walks the run's FULL parent_run_id chain back to its root (not
    just one hop), scores every hop, and reports the MINIMUM per-hop
    score as the overall result — a chain is only as trustworthy as its
    weakest handoff, never its average."""

    name = "handoff_chain"

    def __init__(self, repository: RunRepository, *, max_hops: int = 20) -> None:
        self._repository = repository
        self._max_hops = max_hops

    async def evaluate(self, case: EvalCase) -> EvaluationResult:
        run_id = case.source_run_id
        if not run_id:
            return EvaluationResult(
                evaluation_run_id="",
                metric=self.name,
                available=False,
                reason="no source_run_id on this EvalCase — ChainHandoffEvaluator needs a real run",
            )

        current = await self._repository.get_run(run_id)
        if current is None:
            return EvaluationResult(
                evaluation_run_id="", source_run_id=run_id, metric=self.name,
                available=False, reason="run not found",
            )

        chain = [current]
        hops = 0
        while chain[-1].get("parent_run_id") and hops < self._max_hops:
            parent = await self._repository.get_run(chain[-1]["parent_run_id"])
            if parent is None:
                break
            chain.append(parent)
            hops += 1
        chain.reverse()  # root-first

        if len(chain) < 2:
            return EvaluationResult(
                evaluation_run_id="", source_run_id=run_id, metric=self.name, available=False,
                reason="this run has no parent chain — not a multi-agent handoff, nothing to score",
            )

        hop_scores: list[float] = []
        details: list[str] = []
        for i in range(len(chain) - 1):
            parent_run, child_run = chain[i], chain[i + 1]
            score, lost, altered = _compute_handoff(parent_run.get("final_state") or {}, child_run.get("initial_state") or {})
            hop_scores.append(score)
            hop_label = f"hop {i + 1} ({parent_run['id'][:8]}→{child_run['id'][:8]})"
            details.append(f"{hop_label}: score={score:.2f} ({_describe(lost, altered)})")

        overall = min(hop_scores)
        reason = f"{len(hop_scores)} hop(s), weakest-link score={overall:.2f}. " + " | ".join(details)

        return EvaluationResult(
            evaluation_run_id="", source_run_id=run_id, metric=self.name,
            score=overall, available=True, reason=reason,
        )
