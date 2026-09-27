import pytest

from agentguard.errors import ForbiddenActionError
from agentguard.models import Policy, Run
from agentguard.policy.engine import PolicyEngine, PolicyValidationError


def test_valid_policy_passes():
    PolicyEngine().validate(Policy(max_cost=60000))


def test_negative_max_cost_rejected():
    with pytest.raises(PolicyValidationError):
        PolicyEngine().validate(Policy(max_cost=-1))


def test_no_constraint_is_valid():
    PolicyEngine().validate(Policy())


def test_negative_retry_limit_rejected():
    with pytest.raises(PolicyValidationError):
        PolicyEngine().validate(Policy(retry_limit=-1))


# -- Phase 2: runtime policy evaluation ----------------------------------


def test_max_cost_violation_produces_finding():
    run = Run(agent_name="a", policy=Policy(max_cost=60000))
    run.final_state = {"max_budget": 67000}
    findings = PolicyEngine().evaluate(run)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule == "max_cost"
    assert finding.violated is True
    assert finding.expected == 60000
    assert finding.observed == 67000
    assert finding.severity == "high"


def test_max_cost_within_budget_produces_non_violated_finding():
    run = Run(agent_name="a", policy=Policy(max_cost=60000))
    run.final_state = {"max_budget": 60000}
    findings = PolicyEngine().evaluate(run)
    assert findings[0].violated is False
    assert findings[0].severity == "low"


def test_forbidden_action_raises_immediately():
    policy = Policy(forbidden_actions=["drop_database"])
    with pytest.raises(ForbiddenActionError):
        PolicyEngine().check_action(policy, "drop_database")


def test_require_approval_action_returns_finding_not_raise():
    policy = Policy(require_approval=["payment"])
    finding = PolicyEngine().check_action(policy, "payment")
    assert finding is not None
    assert finding.rule == "require_approval"
    assert finding.violated is True
    assert finding.severity == "medium"


def test_unrestricted_action_returns_none():
    policy = Policy(forbidden_actions=["drop_database"], require_approval=["payment"])
    assert PolicyEngine().check_action(policy, "read_file") is None


def test_default_on_uncertain_fallback_is_configurable():
    assert Policy().default_on_uncertain == "stop"
    assert Policy(default_on_uncertain="human").default_on_uncertain == "human"
