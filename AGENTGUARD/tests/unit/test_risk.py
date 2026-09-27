import pytest

from agentguard.models import EvalResult, PolicyFinding
from agentguard.reliability.confidence import confidence_level, impact_level
from agentguard.reliability.risk import WEIGHTS, RiskEngine


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_all_passed_no_violation_is_low_risk():
    result = EvalResult(evaluator="x", passed=True, score=1.0, label="ok", confidence=1.0)
    assessment = RiskEngine().assess("run-1", eval_results=[result], policy_findings=[])
    assert assessment.risk_score == pytest.approx(0.0)
    assert assessment.impact == pytest.approx(0.0)
    assert assessment.confidence == pytest.approx(1.0)


def test_failed_eval_raises_impact_and_risk():
    result = EvalResult(evaluator="x", passed=False, score=0.0, label="constraint_violated", confidence=1.0)
    assessment = RiskEngine().assess("run-1", eval_results=[result], policy_findings=[])
    assert assessment.impact == pytest.approx(1.0)
    assert assessment.risk_score > 0.0


def test_policy_violation_contributes_to_risk_even_without_eval_failure():
    finding = PolicyFinding(rule="max_cost", violated=True, severity="high", expected=60000, observed=67000)
    result = EvalResult(evaluator="x", passed=True, score=1.0, label="ok", confidence=1.0)
    assessment = RiskEngine().assess("run-1", eval_results=[result], policy_findings=[finding])
    assert assessment.factors["policy_violation"] == 1.0
    assert assessment.risk_score > 0.0


def test_confidence_is_the_minimum_across_evaluators():
    results = [
        EvalResult(evaluator="a", passed=True, score=1.0, label="ok", confidence=0.9),
        EvalResult(evaluator="b", passed=True, score=1.0, label="ok", confidence=0.4),
    ]
    assessment = RiskEngine().assess("run-1", eval_results=results, policy_findings=[])
    assert assessment.confidence == pytest.approx(0.4)


def test_no_eval_results_defaults_to_full_confidence():
    assessment = RiskEngine().assess("run-1", eval_results=[], policy_findings=[])
    assert assessment.confidence == pytest.approx(1.0)


def test_risk_score_is_reproducible_from_factors_and_weights():
    result = EvalResult(evaluator="x", passed=False, score=0.2, label="bad", confidence=0.5)
    finding = PolicyFinding(rule="max_cost", violated=True, severity="critical")
    assessment = RiskEngine().assess("run-1", eval_results=[result], policy_findings=[finding])
    recomputed = sum(assessment.weights[k] * assessment.factors[k] for k in assessment.weights)
    assert assessment.risk_score == pytest.approx(min(1.0, max(0.0, recomputed)))


def test_goal_drift_and_tool_reliability_are_neutral_placeholders():
    result = EvalResult(evaluator="x", passed=True, score=1.0, label="ok", confidence=1.0)
    assessment = RiskEngine().assess("run-1", eval_results=[result], policy_findings=[])
    assert assessment.factors["goal_drift"] == 0.0
    assert assessment.factors["tool_reliability"] == 0.0


# -- confidence/impact level classification --------------------------------


def test_confidence_level_thresholds():
    assert confidence_level(0.9) == "high"
    assert confidence_level(0.7) == "high"
    assert confidence_level(0.69) == "low"
    assert confidence_level(0.1) == "low"


def test_impact_level_thresholds():
    assert impact_level(0.9) == "high"
    assert impact_level(0.6) == "high"
    assert impact_level(0.59) == "low"
    assert impact_level(0.0) == "low"
