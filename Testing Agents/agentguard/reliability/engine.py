"""Reliability Engine — the Phase 2 facade in front of Evaluation,
Root Cause, Risk, and Confidence, matching the architecture diagram in
the Phase 2 spec:

    Reliability Engine
    ├── Evaluation   (evaluators/ — already run by the caller)
    ├── Root Cause   (reliability/root_cause.py)
    ├── Risk         (reliability/risk.py)
    └── Confidence   (reliability/confidence.py)

decorator.py calls this once per run, after evaluators have produced
their EvalResults and PolicyEngine has produced its PolicyFindings, and
hands the resulting ReliabilityAssessment straight to the Decision
Engine. Nothing here decides CONTINUE/RETRY/REPLAN/HUMAN/STOP — that
stays the Decision Engine's job.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import EvalResult, Policy, PolicyFinding, RiskAssessment, RootCause, StateSnapshot
from .confidence import Level, confidence_level, impact_level
from .risk import RiskEngine
from .root_cause import RootCauseEngine


@dataclass
class ReliabilityAssessment:
    risk: RiskAssessment
    root_cause: RootCause | None
    confidence_level: Level
    impact_level: Level


class ReliabilityEngine:
    def __init__(self) -> None:
        self._risk = RiskEngine()
        self._root_cause = RootCauseEngine()

    def assess(
        self,
        run_id: str,
        *,
        policy: Policy,
        eval_results: list[EvalResult],
        policy_findings: list[PolicyFinding],
        state_history: list[StateSnapshot],
        action: str | None = None,
    ) -> ReliabilityAssessment:
        risk = self._risk.assess(
            run_id, eval_results=eval_results, policy_findings=policy_findings, action=action
        )
        root_cause = self._root_cause.analyze(run_id, policy, state_history)
        return ReliabilityAssessment(
            risk=risk,
            root_cause=root_cause,
            confidence_level=confidence_level(risk.confidence),
            impact_level=impact_level(risk.impact),
        )
