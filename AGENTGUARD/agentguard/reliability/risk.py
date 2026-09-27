"""Dynamic per-action Risk Scoring.

The formula is fixed and documented here — not tuned per call — so a
risk_score is always reproducible from its `factors`/`weights`:

    risk_score = w_impact       * impact
               + w_policy       * policy_violation
               + w_uncertainty  * uncertainty
               + w_goal_drift   * goal_drift
               + w_tool_reliab  * tool_unreliability

All factors are normalized to [0, 1]; risk_score is clamped to [0, 1].

`goal_drift` and `tool_reliability` are Phase 3/4 capabilities. Phase 2
does not fake them: both factors are held at a fixed neutral value
(0.0 contribution — see `_NEUTRAL_FACTOR`) and carry a `+` marker in
`factors` so callers can see they are placeholders, not measurements.
"""
from __future__ import annotations

from typing import Any

from ..models import EvalResult, PolicyFinding, RiskAssessment

WEIGHTS: dict[str, float] = {
    "impact": 0.45,
    "policy_violation": 0.30,
    "uncertainty": 0.15,
    "goal_drift": 0.05,
    "tool_reliability": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

_NEUTRAL_FACTOR = 0.0  # Phase 3/4 placeholder value — not a measurement.

_SEVERITY_IMPACT = {"low": 0.25, "medium": 0.5, "high": 0.8, "critical": 1.0}


class RiskEngine:
    def assess(
        self,
        run_id: str,
        *,
        eval_results: list[EvalResult],
        policy_findings: list[PolicyFinding],
        action: str | None = None,
    ) -> RiskAssessment:
        impact = self._impact(eval_results, policy_findings)
        policy_violation = 1.0 if any(f.violated for f in policy_findings) else 0.0
        confidence = self._confidence(eval_results)
        uncertainty = 1.0 - confidence

        factors = {
            "impact": impact,
            "policy_violation": policy_violation,
            "uncertainty": uncertainty,
            "goal_drift": _NEUTRAL_FACTOR,
            "tool_reliability": _NEUTRAL_FACTOR,
        }

        risk_score = sum(WEIGHTS[k] * factors[k] for k in WEIGHTS)
        risk_score = max(0.0, min(1.0, risk_score))

        explanation = (
            f"risk_score={risk_score:.3f} = "
            + " + ".join(f"{WEIGHTS[k]:.2f}*{k}({factors[k]:.2f})" for k in WEIGHTS)
        )

        return RiskAssessment(
            run_id=run_id,
            action=action,
            risk_score=risk_score,
            impact=impact,
            confidence=confidence,
            factors=factors,
            weights=dict(WEIGHTS),
            explanation=explanation,
        )

    def _impact(self, eval_results: list[EvalResult], policy_findings: list[PolicyFinding]) -> float:
        candidates: list[float] = []
        for finding in policy_findings:
            if finding.violated:
                candidates.append(_SEVERITY_IMPACT.get(finding.severity, 0.5))
        for result in eval_results:
            if not result.passed:
                candidates.append(1.0 - result.score)
        return max(candidates) if candidates else 0.0

    def _confidence(self, eval_results: list[EvalResult]) -> float:
        if not eval_results:
            return 1.0
        return min(r.confidence for r in eval_results)
