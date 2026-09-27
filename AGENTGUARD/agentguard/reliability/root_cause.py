"""Causal state-diff Root-Cause Engine.

Deterministic, not LLM-driven (Rule: "do not make the LLM responsible
for determining the root cause when deterministic state data can
determine it"). It inspects the ordered `StateSnapshot` history a run
accumulated (one snapshot per `agentguard.update_state()` call, plus the
seeded initial state) and finds the *earliest* point a policy-tracked
invariant stopped holding — not the final snapshot where a downstream
evaluator happens to notice the violation.

Canonical example (see docs/PHASE2.md):

    Initial / S1 / S2: max_budget = 60000
    S3: max_budget goes missing from state
    S4: agent (re)sets max_budget = 67000 (its "selected product" write
        happens to reuse the same field the constraint lives in)
    S5: ConstraintAdherenceEvaluator flags 67000 > 60000

The naive evaluator only ever looks at the *final* state, so it reports
the violation at S5. But the actual break happened at S3: once the
constraint disappeared from state, nothing was left to stop S4's bad
write. This engine reports S3, with S5 kept only as a fallback when no
earlier disappearance/mutation exists.
"""
from __future__ import annotations

from typing import Any

from ..models import Policy, RootCause, StateSnapshot

_MISSING = object()

# Confidence levels are fixed and documented, not tuned per-run, so a
# reported root cause's confidence is always explainable by which branch
# of the algorithm produced it.
_DISAPPEARANCE_CONFIDENCE = 0.97
_MUTATION_CONFIDENCE = 0.9
_FINAL_STATE_ONLY_CONFIDENCE = 0.7


class RootCauseEngine:
    def analyze(
        self,
        run_id: str,
        policy: Policy,
        state_history: list[StateSnapshot],
        *,
        field: str = "max_budget",
        policy_attr: str = "max_cost",
    ) -> RootCause | None:
        """Return the earliest meaningful deviation, or None if the
        tracked invariant was never broken.
        """
        baseline = getattr(policy, policy_attr, None)
        if baseline is None or not state_history:
            return None

        sequence = [s.label for s in state_history]
        last_known: Any = baseline

        for snapshot in state_history:
            value = snapshot.data.get(field, _MISSING)

            if value is _MISSING:
                if last_known is not None:
                    return RootCause(
                        run_id=run_id,
                        earliest_deviation=snapshot.label,
                        expected={field: last_known},
                        observed={field: None},
                        confidence=_DISAPPEARANCE_CONFIDENCE,
                        explanation=(
                            f"{field} disappeared during state transition {snapshot.label}"
                        ),
                        evidence={"state_sequence": sequence, "policy_attr": policy_attr},
                    )
                # Already known to be missing; nothing new to report here.
                continue

            if value != last_known and last_known is not None and value > baseline:
                # The invariant is still present but was mutated, in one
                # step, past the policy boundary — with no intervening
                # disappearance to explain how that became possible.
                return RootCause(
                    run_id=run_id,
                    earliest_deviation=snapshot.label,
                    expected={field: last_known},
                    observed={field: value},
                    confidence=_MUTATION_CONFIDENCE,
                    explanation=(
                        f"{field} changed from {last_known} to {value} during "
                        f"state transition {snapshot.label}, exceeding policy.{policy_attr}={baseline}"
                    ),
                    evidence={"state_sequence": sequence, "policy_attr": policy_attr},
                )

            last_known = value

        final_value = state_history[-1].data.get(field, _MISSING)
        if final_value is not _MISSING and final_value is not None and final_value > baseline:
            final_label = state_history[-1].label
            return RootCause(
                run_id=run_id,
                earliest_deviation=final_label,
                expected={field: baseline},
                observed={field: final_value},
                confidence=_FINAL_STATE_ONLY_CONFIDENCE,
                explanation=(
                    f"{field}={final_value} violates policy.{policy_attr}={baseline} at the "
                    f"final state; no earlier deviation was detected in the state history"
                ),
                evidence={"state_sequence": sequence, "policy_attr": policy_attr},
            )

        return None
