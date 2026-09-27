"""Root-Cause Engine tests — the canonical "constraint disappears before
the final evaluator notices" scenario from the Phase 2 spec, plus the
"no violation -> no root cause" case.
"""
from agentguard.models import Policy, StateSnapshot
from agentguard.reliability.root_cause import RootCauseEngine


def _snap(label: str, seq: int, data: dict) -> StateSnapshot:
    return StateSnapshot(label=label, seq=seq, data=data)


def test_constraint_lost_at_early_state_is_the_root_cause():
    policy = Policy(max_cost=60000)
    history = [
        _snap("S1", 1, {"max_budget": 60000}),
        _snap("S2", 2, {"max_budget": 60000}),
        _snap("S3", 3, {}),  # max_budget silently disappears
        _snap("S4", 4, {"max_budget": 67000}),  # agent re-sets it, now over budget
    ]

    root_cause = RootCauseEngine().analyze("run-1", policy, history)

    assert root_cause is not None
    assert root_cause.earliest_deviation == "S3"
    assert root_cause.expected == {"max_budget": 60000}
    assert root_cause.observed == {"max_budget": None}
    assert root_cause.confidence >= 0.9


def test_final_failure_differs_from_root_cause():
    # The evaluator would flag S4 (67000 > 60000) as the failure, but the
    # actual, earliest cause is S3 — this is the whole point of the engine.
    policy = Policy(max_cost=60000)
    history = [
        _snap("S1", 1, {"max_budget": 60000}),
        _snap("S2", 2, {"max_budget": 60000}),
        _snap("S3", 3, {}),
        _snap("S4", 4, {"max_budget": 67000}),
    ]
    root_cause = RootCauseEngine().analyze("run-1", policy, history)
    final_violation_label = history[-1].label
    assert root_cause.earliest_deviation != final_violation_label
    assert root_cause.earliest_deviation == "S3"


def test_no_violation_produces_no_root_cause():
    policy = Policy(max_cost=60000)
    history = [
        _snap("S1", 1, {"max_budget": 60000}),
        _snap("S2", 2, {"max_budget": 60000}),
        _snap("S3", 3, {"max_budget": 60000}),
    ]
    assert RootCauseEngine().analyze("run-1", policy, history) is None


def test_no_policy_constraint_produces_no_root_cause():
    policy = Policy()  # no max_cost
    history = [_snap("S1", 1, {"max_budget": 999999})]
    assert RootCauseEngine().analyze("run-1", policy, history) is None


def test_empty_history_produces_no_root_cause():
    policy = Policy(max_cost=60000)
    assert RootCauseEngine().analyze("run-1", policy, []) is None


def test_direct_mutation_past_boundary_without_disappearance():
    policy = Policy(max_cost=60000)
    history = [
        _snap("S1", 1, {"max_budget": 60000}),
        _snap("S2", 2, {"max_budget": 67000}),  # jumps straight over budget, no disappearance
    ]
    root_cause = RootCauseEngine().analyze("run-1", policy, history)
    assert root_cause is not None
    assert root_cause.earliest_deviation == "S2"
    assert root_cause.observed == {"max_budget": 67000}


def test_evidence_includes_full_state_sequence():
    policy = Policy(max_cost=60000)
    history = [
        _snap("S1", 1, {"max_budget": 60000}),
        _snap("S2", 2, {}),
    ]
    root_cause = RootCauseEngine().analyze("run-1", policy, history)
    assert root_cause.evidence["state_sequence"] == ["S1", "S2"]
