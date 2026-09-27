"""Phase 2 Decision Engine — the sole authority for CONTINUE / RETRY /
REPLAN / HUMAN / STOP.

State machine (see docs/PHASE2.md for the full diagram):

    RUNNING
       |
       |-- agent raises TransientError -----------------> RETRY
       |-- agent raises ReplanRequested -----------------> REPLAN
       v
    EVALUATING
       |-- critical policy violation --------------------> STOP
       |-- HIGH confidence + safe ------------------------> CONTINUE
       |-- HIGH confidence + unsafe -----------------------> STOP
       |-- LOW confidence + HIGH impact -------------------> HUMAN
       |-- LOW confidence + LOW impact ---------------------> policy.default_on_uncertain
       v
    HUMAN
       |-- approved ----------------------------------------> CONTINUE
       |-- rejected / timeout -------------------------------> STOP
       |-- replan requested by reviewer ----------------------> REPLAN

Every transition is a guarded method that raises RuntimeError if called
from the wrong state, so "how did this run reach STOP" is always a real,
auditable transition history rather than an implicit side effect of
call order (Phase 1's guarantee, preserved and extended).

Policy logic itself (what counts as "critical", what the uncertainty
fallback is) lives in agentguard.policy — this engine only consumes the
PolicyFinding/RiskAssessment/confidence evidence those components
produce, per the Phase 2 rule against hard-coding policy logic here.
"""
from __future__ import annotations

from typing import Literal

from ..models import Decision, EvalResult, Policy, PolicyFinding, RunStatus
from ..reliability.engine import ReliabilityAssessment

HumanOutcome = Literal["approved", "rejected", "replan", "timeout"]


class DecisionEngine:
    def __init__(self) -> None:
        self.state: RunStatus = RunStatus.RUNNING

    # -- Phase 1 evaluation entry point ---------------------------------

    def begin_evaluation(self) -> RunStatus:
        if self.state != RunStatus.RUNNING:
            raise RuntimeError(f"cannot begin evaluation from state {self.state.value!r}")
        self.state = RunStatus.EVALUATING
        return self.state

    # -- RETRY / REPLAN: reachable directly from RUNNING (the agent
    # raised before there was anything to evaluate) or from EVALUATING
    # (an evaluator flagged a transient/strategic failure). -------------

    def retry(self, *, reason: str, retry_count: int, evidence: dict | None = None) -> Decision:
        if self.state not in (RunStatus.RUNNING, RunStatus.EVALUATING):
            raise RuntimeError(f"cannot retry from state {self.state.value!r}")
        self.state = RunStatus.RETRY
        return Decision(outcome=RunStatus.RETRY, reason=reason, evidence=evidence or {}, retry_count=retry_count)

    def replan(
        self, *, reason: str, replan_count: int, evidence: dict | None = None
    ) -> Decision:
        if self.state not in (RunStatus.RUNNING, RunStatus.EVALUATING, RunStatus.RETRY, RunStatus.HUMAN):
            raise RuntimeError(f"cannot replan from state {self.state.value!r}")
        self.state = RunStatus.REPLAN
        return Decision(outcome=RunStatus.REPLAN, reason=reason, evidence=evidence or {}, replan_count=replan_count)

    def stop(self, *, reason: str, evidence: dict | None = None) -> Decision:
        """Used for the retry/replan-exhausted -> STOP fallback, which is
        not a normal EVALUATING-branch STOP (see decide())."""
        if self.state not in (RunStatus.RUNNING, RunStatus.EVALUATING, RunStatus.RETRY, RunStatus.REPLAN, RunStatus.HUMAN):
            raise RuntimeError(f"cannot stop from state {self.state.value!r}")
        self.state = RunStatus.STOP
        return Decision(outcome=RunStatus.STOP, reason=reason, evidence=evidence or {})

    # -- Phase 3: rollback -------------------------------------------------

    def rollback(self, *, reason: str, evidence: dict | None = None) -> Decision:
        """A completed run's state is being restored to an earlier
        checkpoint (agentguard.recovery.rollback). Reachable only from a
        state representing a run that has actually finished executing —
        never mid-flight (RUNNING/EVALUATING/RETRY/REPLAN), which would
        mean racing a still-suspended agent coroutine."""
        if self.state in (RunStatus.RUNNING, RunStatus.EVALUATING, RunStatus.RETRY, RunStatus.REPLAN):
            raise RuntimeError(f"cannot roll back a run still in-flight (state={self.state.value!r})")
        self.state = RunStatus.ROLLED_BACK
        return Decision(outcome=RunStatus.ROLLED_BACK, reason=reason, evidence=evidence or {})

    # -- HUMAN gate and its resolution -----------------------------------

    def human(
        self, *, reason: str, risk_score: float, confidence: float, evidence: dict | None = None
    ) -> Decision:
        if self.state != RunStatus.EVALUATING:
            raise RuntimeError(f"cannot request human review from state {self.state.value!r}")
        self.state = RunStatus.HUMAN
        return Decision(
            outcome=RunStatus.HUMAN, reason=reason, risk_score=risk_score, confidence=confidence, evidence=evidence or {}
        )

    def resolve_human(self, outcome: HumanOutcome, *, reason: str, evidence: dict | None = None) -> Decision:
        if self.state != RunStatus.HUMAN:
            raise RuntimeError(f"cannot resolve human review from state {self.state.value!r}")
        if outcome == "replan":
            self.state = RunStatus.REPLAN
            return Decision(outcome=RunStatus.REPLAN, reason=reason, evidence=evidence or {})
        if outcome in ("rejected", "timeout"):
            self.state = RunStatus.STOP
            return Decision(outcome=RunStatus.STOP, reason=reason, evidence=evidence or {})
        self.state = RunStatus.CONTINUE
        return Decision(outcome=RunStatus.CONTINUE, reason=reason, evidence=evidence or {})

    # -- The main evaluation-branch decision -----------------------------

    def decide(
        self,
        eval_results: list[EvalResult],
        *,
        policy_findings: list[PolicyFinding] | None = None,
        reliability: ReliabilityAssessment | None = None,
        policy: Policy | None = None,
    ) -> Decision:
        """Phase 1 signature (`decide(eval_results)`) still works: with no
        policy_findings/reliability/policy supplied, this falls back to
        Phase 1's exact rule (STOP if any evaluator failed, else
        CONTINUE) — backward compatibility per the Phase 2 spec.

        With the Phase 2 arguments supplied, applies the full routing
        table documented in this module's docstring.
        """
        if self.state != RunStatus.EVALUATING:
            raise RuntimeError(f"cannot decide from state {self.state.value!r}")

        policy_findings = policy_findings or []
        failed = [r for r in eval_results if not r.passed]

        if reliability is None or policy is None:
            # Phase 1 behavior, unchanged.
            if failed:
                self.state = RunStatus.STOP
                return Decision(
                    outcome=RunStatus.STOP,
                    reason=f"{len(failed)} evaluator(s) failed: " + ", ".join(r.evaluator for r in failed),
                    evidence={"failed": [r.model_dump() for r in failed]},
                )
            self.state = RunStatus.CONTINUE
            return Decision(
                outcome=RunStatus.CONTINUE,
                reason="all evaluators passed",
                evidence={"evaluations": [r.model_dump() for r in eval_results]},
            )

        base_evidence = {
            "evaluations": [r.model_dump() for r in eval_results],
            "policy_findings": [f.model_dump() for f in policy_findings],
            "risk": reliability.risk.model_dump(),
        }

        critical = [f for f in policy_findings if f.violated and f.severity == "critical"]
        if critical:
            self.state = RunStatus.STOP
            return Decision(
                outcome=RunStatus.STOP,
                reason="critical policy violation: " + "; ".join(f.detail or f.rule for f in critical),
                evidence=base_evidence,
                risk_score=reliability.risk.risk_score,
                confidence=reliability.risk.confidence,
                policy_findings=policy_findings,
            )

        unsafe = bool(failed) or any(f.violated for f in policy_findings)

        if reliability.confidence_level == "high":
            if unsafe:
                self.state = RunStatus.STOP
                return Decision(
                    outcome=RunStatus.STOP,
                    reason="high-confidence unsafe evaluation: "
                    + ", ".join(r.evaluator for r in failed)
                    if failed
                    else "high-confidence policy violation",
                    evidence=base_evidence,
                    risk_score=reliability.risk.risk_score,
                    confidence=reliability.risk.confidence,
                    policy_findings=policy_findings,
                )
            self.state = RunStatus.CONTINUE
            return Decision(
                outcome=RunStatus.CONTINUE,
                reason="high-confidence safe evaluation",
                evidence=base_evidence,
                risk_score=reliability.risk.risk_score,
                confidence=reliability.risk.confidence,
                policy_findings=policy_findings,
            )

        # LOW confidence -----------------------------------------------
        if reliability.impact_level == "high":
            self.state = RunStatus.HUMAN
            return Decision(
                outcome=RunStatus.HUMAN,
                reason="low-confidence evaluation on a high-impact action",
                evidence=base_evidence,
                risk_score=reliability.risk.risk_score,
                confidence=reliability.risk.confidence,
                policy_findings=policy_findings,
            )

        fallback = policy.default_on_uncertain
        outcome = {"continue": RunStatus.CONTINUE, "stop": RunStatus.STOP, "human": RunStatus.HUMAN}[fallback]
        self.state = outcome
        return Decision(
            outcome=outcome,
            reason=f"low-confidence, low-impact evaluation: policy.default_on_uncertain={fallback!r}",
            evidence=base_evidence,
            risk_score=reliability.risk.risk_score,
            confidence=reliability.risk.confidence,
            policy_findings=policy_findings,
        )
