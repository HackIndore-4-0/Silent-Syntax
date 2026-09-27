"""Policy validation and evaluation.

Two separate concerns live here, both deliberately kept out of the
Decision Engine (Rule: "do not hard-code policy logic inside the
Decision Engine — policy evaluation must be a separate component"):

- `validate(policy)` — shape validation at decoration time (Phase 1
  behavior, extended for the new Phase 2 fields).
- `evaluate(run, action=...)` / `check_action(policy, action)` — real
  runtime policy enforcement, producing structured `PolicyFinding`s
  that the Decision Engine consumes as evidence rather than deriving
  itself.

`check_action` runs synchronously and locally (no I/O), so it is safe
on the synchronous critical path.
"""
from __future__ import annotations

from ..errors import ForbiddenActionError
from ..models import Policy, PolicyFinding, Run


class PolicyValidationError(ValueError):
    pass


class PolicyEngine:
    def validate(self, policy: Policy) -> None:
        if policy.max_cost is not None and policy.max_cost < 0:
            raise PolicyValidationError("policy.max_cost must be >= 0")
        if policy.retry_limit < 0:
            raise PolicyValidationError("policy.retry_limit must be >= 0")
        if policy.max_replans < 0:
            raise PolicyValidationError("policy.max_replans must be >= 0")
        if policy.human_timeout_s <= 0:
            raise PolicyValidationError("policy.human_timeout_s must be > 0")
        if policy.circuit_breaker is not None:
            cb = policy.circuit_breaker
            if cb.max_consecutive_tool_failures is not None and cb.max_consecutive_tool_failures < 1:
                raise PolicyValidationError("policy.circuit_breaker.max_consecutive_tool_failures must be >= 1")
            if cb.max_loop_iterations is not None and cb.max_loop_iterations < 1:
                raise PolicyValidationError("policy.circuit_breaker.max_loop_iterations must be >= 1")

    def check_action(self, policy: Policy, action: str) -> PolicyFinding | None:
        """Synchronous, local guardrail: raises ForbiddenActionError the
        moment a forbidden action is attempted. Returns a PolicyFinding
        (not raised) when the action merely requires human approval, so
        the caller can route to HUMAN rather than abort immediately.
        """
        if action in policy.forbidden_actions:
            raise ForbiddenActionError(action)
        if action in policy.require_approval:
            return PolicyFinding(
                rule="require_approval",
                violated=True,
                severity="medium",
                expected="human approval",
                observed="not yet approved",
                detail=f"action {action!r} is listed in policy.require_approval",
            )
        return None

    def evaluate(
        self,
        run: Run,
        *,
        field: str = "max_budget",
        policy_attr: str = "max_cost",
    ) -> list[PolicyFinding]:
        """Post-execution declarative policy check against a run's final
        state. Returns one PolicyFinding per rule that applies (violated
        or not) so the caller has a complete, auditable picture — not
        just the failures.
        """
        findings: list[PolicyFinding] = []
        policy = run.policy

        expected = getattr(policy, policy_attr, None)
        if expected is not None:
            final_state = run.final_state or {}
            observed = final_state.get(field)
            if observed is not None:
                violated = observed > expected
                findings.append(
                    PolicyFinding(
                        rule=policy_attr,
                        violated=violated,
                        severity="high" if violated else "low",
                        expected=expected,
                        observed=observed,
                        detail=(
                            f"{field}={observed} exceeds policy.{policy_attr}={expected}"
                            if violated
                            else f"{field}={observed} is within policy.{policy_attr}={expected}"
                        ),
                    )
                )

        return findings
