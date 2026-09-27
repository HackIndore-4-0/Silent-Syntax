"""Deterministic, rule-based recommendation generation — Phase 4.

Turns a persisted `RootCause` into a structured `Recommendation`
(problem, root cause, recommendation text, before/after candidate
change, expected benefit). This is NOT DSPy — it is the deterministic
step that FEEDS a PromptOptimizer (agentguard/improve/optimizer.py);
DSPy (or its deterministic stand-in) turns a Recommendation into an
ImprovementCandidate.

Rules are explicit and documented, matching the exact canonical example
in the Phase 4 spec (§18) — a value silently "disappearing" from state
recommends making it an immutable, typed constraint rather than
something re-derivable from a prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Recommendation:
    problem: str
    root_cause_summary: str
    recommendation: str
    candidate_change: dict[str, str]
    expected_benefit: str


def generate_recommendation(root_cause: dict[str, Any] | None) -> Recommendation:
    if root_cause is None:
        return Recommendation(
            problem="No root cause was recorded for this run.",
            root_cause_summary="",
            recommendation="No deterministic recommendation available without a recorded RootCause.",
            candidate_change={},
            expected_benefit="",
        )

    explanation = root_cause.get("explanation", "")
    field = (root_cause.get("evidence") or {}).get("policy_attr")
    expected = root_cause.get("expected") or {}
    field_name = next(iter(expected), field or "constraint")

    if "disappeared" in explanation:
        return Recommendation(
            problem=f"{field_name} lost during state transition {root_cause.get('earliest_deviation')}.",
            root_cause_summary=explanation,
            recommendation=f"Preserve {field_name} as an immutable constraint in the planning state.",
            candidate_change={
                "before": f"{field_name} embedded only in prompt/state and vulnerable to being overwritten by reset_state()",
                "after": f"{field_name} stored as a validated, typed AgentState field that survives state resets",
            },
            expected_benefit=f"Prevents the {field_name} constraint from silently disappearing before it can be enforced.",
        )

    if "exceeding" in explanation or "changed from" in explanation:
        return Recommendation(
            problem=f"{field_name} was mutated past its policy boundary in a single step at {root_cause.get('earliest_deviation')}.",
            root_cause_summary=explanation,
            recommendation=f"Validate {field_name} against policy.max_cost at the point of mutation, not only post-hoc.",
            candidate_change={
                "before": f"{field_name} updates are unchecked at the point of mutation",
                "after": f"{field_name} updates run through a bounds check before being accepted",
            },
            expected_benefit="Catches an over-limit selection immediately, before it becomes the run's final state.",
        )

    return Recommendation(
        problem=f"Constraint violated at {root_cause.get('earliest_deviation')}.",
        root_cause_summary=explanation,
        recommendation="Review the state-transition logic around the flagged deviation point.",
        candidate_change={"before": "unspecified", "after": "unspecified"},
        expected_benefit="Requires manual review — no deterministic rule matched this root cause pattern.",
    )
