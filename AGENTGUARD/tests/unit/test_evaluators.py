from agentguard.evaluators.rule_based import ConstraintAdherenceEvaluator
from agentguard.models import Policy, Run


def _run(max_cost, final_max_budget):
    run = Run(agent_name="a", policy=Policy(max_cost=max_cost))
    run.final_state = {"max_budget": final_max_budget} if final_max_budget is not None else {}
    return run


def test_constraint_within_budget_passes():
    evaluator = ConstraintAdherenceEvaluator()
    result = evaluator.evaluate(_run(60000, 60000))
    assert result.passed is True
    assert result.score == 1.0
    assert result.label == "ok"


def test_constraint_exceeded_fails():
    evaluator = ConstraintAdherenceEvaluator()
    result = evaluator.evaluate(_run(60000, 67000))
    assert result.passed is False
    assert result.score == 0.0
    assert result.label == "constraint_violated"
    assert result.evidence["expected"] == 60000
    assert result.evidence["observed"] == 67000


def test_no_policy_constraint_is_not_applicable():
    evaluator = ConstraintAdherenceEvaluator()
    run = Run(agent_name="a")
    run.final_state = {"max_budget": 99999}
    result = evaluator.evaluate(run)
    assert result.passed is True
    assert result.label == "not_applicable"


def test_missing_final_state_field_is_not_applicable():
    evaluator = ConstraintAdherenceEvaluator()
    result = evaluator.evaluate(_run(60000, None))
    assert result.passed is True
    assert result.label == "not_applicable"


def test_custom_field_and_policy_attr():
    evaluator = ConstraintAdherenceEvaluator(field="spend", policy_attr="max_cost")
    run = Run(agent_name="a", policy=Policy(max_cost=100))
    run.final_state = {"spend": 150}
    result = evaluator.evaluate(run)
    assert result.passed is False
    assert result.evidence["field"] == "spend"
