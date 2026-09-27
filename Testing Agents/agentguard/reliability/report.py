"""Six-dimension Reliability Report — Phase 3.

Builds the report from a run's already-persisted, real evaluation data
(the same dashboard-shaped dict `RunRepository.get_run()` returns) —
never from arbitrary placeholder numbers. Each dimension records its own
`source` string documenting exactly which mechanism produced it, and
`available=False` (value=None) when the underlying data genuinely
doesn't exist for that run, rather than fabricating a number.

Dimension -> data source:

    1. Correctness            llm_judge evidence["correctness"] (pass/fail),
                               falling back to the deterministic
                               constraint_adherence evaluator's `passed`
                               when no LLM judge evaluation is present —
                               labeled which one actually produced it.
    2. Goal Completion        llm_judge evidence["goal_completion"]
                               (pass/fail). Unavailable if no LLM judge
                               evaluation is present (never fabricated).
    3. Constraint Adherence   the deterministic constraint_adherence
                               evaluator's score (1.0 pass / 0.0 fail),
                               falling back to the fraction of
                               non-violated declarative PolicyFindings if
                               that evaluator didn't run.
    4. Decision Consistency   documented penalty formula over the run's
                               own recorded Decision sequence (see
                               _decision_consistency below) — RETRY/REPLAN
                               loops and post-hoc LLM-judge overrides each
                               cost a fixed, documented penalty.
    5. Tool Usage             fraction of agentguard.perform_action()
                               calls (persisted on Run.actions) that did
                               not end in a forbidden/rejected outcome;
                               1.0 (neutral) when no actions were taken.
    6. Behavioral Reliability documented aggregation: the arithmetic mean
                               of whichever of dimensions 1-5 are
                               available (unavailable ones are excluded
                               from the average, never treated as 0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Dimension:
    value: float | None
    available: bool
    source: str


@dataclass
class ReliabilityReport:
    run_id: str
    status: str
    correctness: Dimension
    goal_completion: Dimension
    constraint_adherence: Dimension
    decision_consistency: Dimension
    tool_usage: Dimension
    behavioral_reliability: Dimension
    risk: float | None
    confidence: float | None
    recovery_status: str
    root_cause_summary: str | None
    interventions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _d(dim: Dimension) -> dict[str, Any]:
            return {"value": dim.value, "available": dim.available, "source": dim.source}

        return {
            "run_id": self.run_id,
            "status": self.status,
            "dimensions": {
                "correctness": _d(self.correctness),
                "goal_completion": _d(self.goal_completion),
                "constraint_adherence": _d(self.constraint_adherence),
                "decision_consistency": _d(self.decision_consistency),
                "tool_usage": _d(self.tool_usage),
                "behavioral_reliability": _d(self.behavioral_reliability),
            },
            "risk": self.risk,
            "confidence": self.confidence,
            "recovery": {"status": self.recovery_status},
            "root_cause_summary": self.root_cause_summary,
            "interventions": self.interventions,
        }


_RETRY_PENALTY = 0.10
_REPLAN_PENALTY = 0.15
_OVERRIDE_PENALTY = 0.25
_HUMAN_ESCALATION_PENALTY = 0.10


def _pass_fail_to_score(value: Any) -> float | None:
    if value == "pass":
        return 1.0
    if value == "fail":
        return 0.0
    return None


def _correctness(evaluations: list[dict[str, Any]]) -> Dimension:
    judge = next((e for e in evaluations if e.get("evaluator") == "llm_judge"), None)
    if judge is not None:
        score = _pass_fail_to_score((judge.get("evidence") or {}).get("correctness"))
        if score is not None:
            return Dimension(score, True, "llm_judge evidence.correctness")
    constraint = next((e for e in evaluations if e.get("evaluator") == "constraint_adherence"), None)
    if constraint is not None and constraint.get("label") != "not_applicable":
        return Dimension(
            1.0 if constraint.get("passed") else 0.0,
            True,
            "derived_from_constraint_adherence (llm_judge unavailable)",
        )
    return Dimension(None, False, "no correctness signal available (no llm_judge, no applicable constraint evaluator)")


def _goal_completion(evaluations: list[dict[str, Any]]) -> Dimension:
    judge = next((e for e in evaluations if e.get("evaluator") == "llm_judge"), None)
    if judge is not None:
        score = _pass_fail_to_score((judge.get("evidence") or {}).get("goal_completion"))
        if score is not None:
            return Dimension(score, True, "llm_judge evidence.goal_completion")
    return Dimension(None, False, "no llm_judge evaluation available")


def _constraint_adherence(evaluations: list[dict[str, Any]], policy_findings: list[dict[str, Any]]) -> Dimension:
    constraint = next((e for e in evaluations if e.get("evaluator") == "constraint_adherence"), None)
    if constraint is not None and constraint.get("label") != "not_applicable":
        return Dimension(float(constraint.get("score", 0.0)), True, "constraint_adherence evaluator score")
    if policy_findings:
        non_violated = sum(1 for f in policy_findings if not f.get("violated"))
        return Dimension(non_violated / len(policy_findings), True, "fraction of non-violated PolicyFindings")
    return Dimension(None, False, "no constraint evaluator ran and no policy findings recorded")


def _decision_consistency(decisions: list[dict[str, Any]]) -> Dimension:
    if not decisions:
        return Dimension(None, False, "no decisions recorded")
    score = 1.0
    penalties: list[str] = []
    outcomes = [d.get("outcome") for d in decisions]
    for outcome in outcomes:
        if outcome == "retry":
            score -= _RETRY_PENALTY
            penalties.append("retry")
        elif outcome == "replan":
            score -= _REPLAN_PENALTY
            penalties.append("replan")
        elif outcome == "human":
            score -= _HUMAN_ESCALATION_PENALTY
            penalties.append("human_escalation")
    # A post-hoc override looks like CONTINUE followed later by STOP for
    # the same run (decorator.py's async LLM-judge override path) — a
    # concrete, documented contradiction signal, not a guess.
    if "continue" in outcomes and outcomes[-1] == "stop" and outcomes.index("continue") < len(outcomes) - 1:
        score -= _OVERRIDE_PENALTY
        penalties.append("post_hoc_override")
    score = max(0.0, min(1.0, score))
    source = "1.0 minus documented penalties per RETRY(-0.10)/REPLAN(-0.15)/HUMAN(-0.10)/override(-0.25) in the run's own decision sequence"
    if penalties:
        source += f"; applied: {penalties}"
    return Dimension(score, True, source)


def _tool_usage(actions: list[dict[str, Any]]) -> Dimension:
    if not actions:
        return Dimension(1.0, True, "no actions recorded (neutral default — nothing failed)")
    succeeded = 0
    for a in actions:
        human_decision = a.get("human_decision") or {}
        if human_decision.get("status") == "rejected":
            continue
        if human_decision.get("status") == "timeout":
            continue
        succeeded += 1
    return Dimension(succeeded / len(actions), True, "fraction of Run.actions not rejected/timed-out")


def _behavioral_reliability(dims: list[Dimension]) -> Dimension:
    available = [d.value for d in dims if d.available and d.value is not None]
    if not available:
        return Dimension(None, False, "no underlying dimensions available")
    value = sum(available) / len(available)
    return Dimension(
        value,
        True,
        f"arithmetic mean of {len(available)}/{len(dims)} available dimensions (unavailable dimensions excluded, not zeroed)",
    )


def _recovery_status(run: dict[str, Any], has_rollback: bool) -> str:
    status = run.get("status")
    if run.get("parent_run_id") and status == "continue":
        # This run IS a recovery run (seeded from a checkpoint via
        # agentguard.recovery.seed_recovery_state) and it succeeded.
        return "RECOVERED"
    if status == "rolled_back":
        return "ROLLBACK_PENDING_RECOVERY"
    if has_rollback:
        return "ROLLBACK_INITIATED"
    if status == "continue":
        return "NOT_NEEDED"
    if status in ("stop", "failed"):
        return "UNRECOVERED"
    return "IN_PROGRESS"


def build_reliability_report(
    run: dict[str, Any],
    *,
    risk_assessments: list[dict[str, Any]],
    root_cause: dict[str, Any] | None,
    audit_events: list[dict[str, Any]] | None = None,
) -> ReliabilityReport:
    evaluations = run.get("evaluations") or []
    decisions = run.get("decisions") or []
    actions = run.get("actions") or []
    policy_findings: list[dict[str, Any]] = []
    for d in decisions:
        policy_findings.extend((d.get("evidence") or {}).get("policy_findings") or [])

    correctness = _correctness(evaluations)
    goal_completion = _goal_completion(evaluations)
    constraint_adherence = _constraint_adherence(evaluations, policy_findings)
    decision_consistency = _decision_consistency(decisions)
    tool_usage = _tool_usage(actions)
    behavioral_reliability = _behavioral_reliability(
        [correctness, goal_completion, constraint_adherence, decision_consistency, tool_usage]
    )

    latest_risk = risk_assessments[-1] if risk_assessments else None
    audit_events = audit_events or []
    has_rollback = any(e.get("event_type") == "ROLLBACK" for e in audit_events)

    interventions: list[dict[str, Any]] = []
    for d in decisions:
        if d.get("outcome") in ("human", "rolled_back"):
            interventions.append({"type": d.get("outcome"), "reason": d.get("reason"), "created_at": d.get("created_at")})
    for e in audit_events:
        if e.get("event_type") == "ROLLBACK":
            interventions.append({"type": "rollback", "reason": (e.get("payload") or {}).get("reason"), "created_at": e.get("created_at")})

    return ReliabilityReport(
        run_id=run.get("id"),
        status=run.get("status"),
        correctness=correctness,
        goal_completion=goal_completion,
        constraint_adherence=constraint_adherence,
        decision_consistency=decision_consistency,
        tool_usage=tool_usage,
        behavioral_reliability=behavioral_reliability,
        risk=latest_risk.get("risk_score") if latest_risk else None,
        confidence=latest_risk.get("confidence") if latest_risk else None,
        recovery_status=_recovery_status(run, has_rollback),
        root_cause_summary=root_cause.get("explanation") if root_cause else None,
        interventions=interventions,
    )
