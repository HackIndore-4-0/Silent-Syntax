import pytest

from agentguard.decision.engine import DecisionEngine
from agentguard.models import EvalResult, Policy, PolicyFinding, RunStatus
from agentguard.reliability.engine import ReliabilityAssessment
from agentguard.reliability.risk import RiskEngine


def _passed():
    return EvalResult(evaluator="constraint_adherence", passed=True, score=1.0, label="ok", confidence=1.0)


def _failed(confidence: float = 1.0):
    return EvalResult(
        evaluator="constraint_adherence",
        passed=False,
        score=0.0,
        label="constraint_violated",
        confidence=confidence,
    )


def _reliability(eval_results, policy_findings=None, action=None) -> ReliabilityAssessment:
    from agentguard.reliability.confidence import confidence_level, impact_level

    risk = RiskEngine().assess(
        "run-1", eval_results=eval_results, policy_findings=policy_findings or [], action=action
    )
    return ReliabilityAssessment(
        risk=risk,
        root_cause=None,
        confidence_level=confidence_level(risk.confidence),
        impact_level=impact_level(risk.impact),
    )


# -- Phase 1 behavior, preserved (positional-only call, no Phase 2 args) --


def test_all_passed_continues():
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide([_passed()])
    assert decision.outcome == RunStatus.CONTINUE
    assert engine.state == RunStatus.CONTINUE


def test_any_failed_stops():
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide([_passed(), _failed()])
    assert decision.outcome == RunStatus.STOP
    assert engine.state == RunStatus.STOP
    assert "constraint_adherence" in decision.reason


def test_cannot_decide_before_evaluating():
    engine = DecisionEngine()
    with pytest.raises(RuntimeError):
        engine.decide([_passed()])


def test_cannot_begin_evaluation_twice():
    engine = DecisionEngine()
    engine.begin_evaluation()
    with pytest.raises(RuntimeError):
        engine.begin_evaluation()


def test_new_engine_starts_running():
    engine = DecisionEngine()
    assert engine.state == RunStatus.RUNNING


# -- Phase 2: RETRY ---------------------------------------------------------


def test_retry_from_running():
    engine = DecisionEngine()
    decision = engine.retry(reason="transient tool failure", retry_count=1)
    assert decision.outcome == RunStatus.RETRY
    assert decision.retry_count == 1
    assert engine.state == RunStatus.RETRY


def test_retry_limit_is_caller_enforced_not_infinite():
    # The engine itself just records transitions; decorator.py enforces
    # policy.retry_limit. Simulate the bound being hit by the caller.
    policy = Policy(retry_limit=2)
    engine = DecisionEngine()
    attempts = 0
    for i in range(1, policy.retry_limit + 1):
        decision = engine.retry(reason="transient", retry_count=i)
        assert decision.outcome == RunStatus.RETRY
        attempts += 1
        engine = DecisionEngine()  # a fresh attempt, as decorator.py does
    assert attempts == policy.retry_limit


# -- Phase 2: REPLAN ---------------------------------------------------------


def test_replan_from_running():
    engine = DecisionEngine()
    decision = engine.replan(reason="goal drift detected", replan_count=1)
    assert decision.outcome == RunStatus.REPLAN
    assert decision.replan_count == 1
    assert engine.state == RunStatus.REPLAN


def test_replan_from_evaluating():
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.replan(reason="strategic failure", replan_count=1)
    assert decision.outcome == RunStatus.REPLAN


# -- Phase 2: HUMAN -----------------------------------------------------------


def test_low_confidence_high_impact_routes_to_human():
    reliability = _reliability([_failed(confidence=0.2)])
    assert reliability.confidence_level == "low"
    assert reliability.impact_level == "high"

    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide(
        [_failed(confidence=0.2)],
        policy_findings=[],
        reliability=reliability,
        policy=Policy(),
    )
    assert decision.outcome == RunStatus.HUMAN
    assert decision.confidence == pytest.approx(0.2)


def test_human_approval_resumes_to_continue():
    engine = DecisionEngine()
    engine.begin_evaluation()
    engine.human(reason="needs review", risk_score=0.8, confidence=0.2)
    decision = engine.resolve_human("approved", reason="looks fine")
    assert decision.outcome == RunStatus.CONTINUE
    assert engine.state == RunStatus.CONTINUE


def test_human_rejection_stops():
    engine = DecisionEngine()
    engine.begin_evaluation()
    engine.human(reason="needs review", risk_score=0.8, confidence=0.2)
    decision = engine.resolve_human("rejected", reason="too risky")
    assert decision.outcome == RunStatus.STOP


def test_human_timeout_stops():
    engine = DecisionEngine()
    engine.begin_evaluation()
    engine.human(reason="needs review", risk_score=0.8, confidence=0.2)
    decision = engine.resolve_human("timeout", reason="no response within timeout_s")
    assert decision.outcome == RunStatus.STOP


def test_human_replan_request():
    engine = DecisionEngine()
    engine.begin_evaluation()
    engine.human(reason="needs review", risk_score=0.8, confidence=0.2)
    decision = engine.resolve_human("replan", reason="try a different approach")
    assert decision.outcome == RunStatus.REPLAN


def test_cannot_resolve_human_without_pending_request():
    engine = DecisionEngine()
    with pytest.raises(RuntimeError):
        engine.resolve_human("approved", reason="x")


# -- Phase 2: confidence / impact routing table ------------------------------


def test_high_confidence_safe_continues():
    reliability = _reliability([_passed()])
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide([_passed()], policy_findings=[], reliability=reliability, policy=Policy())
    assert decision.outcome == RunStatus.CONTINUE


def test_high_confidence_unsafe_stops():
    reliability = _reliability([_failed(confidence=0.95)])
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide(
        [_failed(confidence=0.95)], policy_findings=[], reliability=reliability, policy=Policy()
    )
    assert decision.outcome == RunStatus.STOP


def test_low_confidence_low_impact_uses_policy_fallback_continue():
    # A low-confidence result with nothing failed/violated keeps impact low.
    result = EvalResult(evaluator="x", passed=True, score=0.9, label="ok", confidence=0.3)
    reliability = _reliability([result])
    assert reliability.confidence_level == "low"
    assert reliability.impact_level == "low"

    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide(
        [result], policy_findings=[], reliability=reliability, policy=Policy(default_on_uncertain="continue")
    )
    assert decision.outcome == RunStatus.CONTINUE


def test_low_confidence_low_impact_uses_policy_fallback_stop():
    result = EvalResult(evaluator="x", passed=True, score=0.9, label="ok", confidence=0.3)
    reliability = _reliability([result])
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide(
        [result], policy_findings=[], reliability=reliability, policy=Policy(default_on_uncertain="stop")
    )
    assert decision.outcome == RunStatus.STOP


def test_uncertainty_never_silently_becomes_safe_by_default():
    # Default policy is default_on_uncertain="stop" — low confidence
    # must never fall through to CONTINUE unless explicitly configured.
    result = EvalResult(evaluator="x", passed=True, score=0.9, label="ok", confidence=0.1)
    reliability = _reliability([result])
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide([result], policy_findings=[], reliability=reliability, policy=Policy())
    assert decision.outcome != RunStatus.CONTINUE


def test_critical_policy_violation_stops_even_with_high_confidence():
    finding = PolicyFinding(rule="forbidden_action", violated=True, severity="critical", detail="drop_database")
    reliability = _reliability([_passed()], policy_findings=[finding])
    engine = DecisionEngine()
    engine.begin_evaluation()
    decision = engine.decide(
        [_passed()], policy_findings=[finding], reliability=reliability, policy=Policy()
    )
    assert decision.outcome == RunStatus.STOP
    assert "critical" in decision.reason.lower()
