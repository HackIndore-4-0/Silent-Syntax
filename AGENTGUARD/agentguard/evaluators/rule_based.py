"""Deterministic, rule-based evaluators.

Phase 1 implements exactly one: ConstraintAdherenceEvaluator. It never
makes a network call or invokes a model, which is what makes it safe to
run on AgentGuard's synchronous critical path (Rule 7).
"""
from __future__ import annotations

from ..models import EvalResult, Run
from .base import Evaluator


class ConstraintAdherenceEvaluator(Evaluator):
    """Checks a single numeric constraint declared on the run's Policy
    against the matching field recorded in the run's final state.

    Example (the canonical AgentGuard demo scenario):
        Policy(max_cost=60000), final_state={"max_budget": 60000} -> PASS
        Policy(max_cost=60000), final_state={"max_budget": 67000} -> FAIL

    `field` and `policy_attr` are configurable so later evaluators can
    check other constraint-carrying state keys without subclassing.
    """

    name = "constraint_adherence"

    def __init__(self, field: str = "max_budget", policy_attr: str = "max_cost"):
        self.field = field
        self.policy_attr = policy_attr

    def evaluate(self, run: Run) -> EvalResult:
        expected = getattr(run.policy, self.policy_attr, None)
        if expected is None:
            return EvalResult(
                evaluator=self.name,
                passed=True,
                score=1.0,
                label="not_applicable",
                evidence={"reason": f"policy.{self.policy_attr} is not set"},
            )

        final_state = run.final_state or {}
        observed = final_state.get(self.field)
        if observed is None:
            return EvalResult(
                evaluator=self.name,
                passed=True,
                score=1.0,
                label="not_applicable",
                evidence={"reason": f"final_state.{self.field} was not recorded"},
            )

        passed = observed <= expected
        return EvalResult(
            evaluator=self.name,
            passed=passed,
            score=1.0 if passed else 0.0,
            label="ok" if passed else "constraint_violated",
            evidence={
                "field": self.field,
                "policy_attr": self.policy_attr,
                "expected": expected,
                "observed": observed,
            },
        )
