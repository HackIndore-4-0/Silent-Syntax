from __future__ import annotations

from agentguard.reliability.report import build_reliability_report


def _base_run(**overrides) -> dict:
    run = {
        "id": "run-1",
        "status": "continue",
        "evaluations": [],
        "decisions": [],
        "actions": [],
        "parent_run_id": None,
    }
    run.update(overrides)
    return run


def test_six_dimensions_populated_from_real_evaluation_data():
    run = _base_run(
        status="continue",
        evaluations=[
            {
                "evaluator": "constraint_adherence",
                "passed": True,
                "score": 1.0,
                "label": "ok",
                "confidence": 1.0,
                "evidence": {},
            },
            {
                "evaluator": "llm_judge",
                "passed": True,
                "score": 0.9,
                "label": "safe",
                "confidence": 0.9,
                "evidence": {"correctness": "pass", "goal_completion": "pass"},
            },
        ],
        decisions=[{"outcome": "continue", "reason": "all evaluators passed", "evidence": {}}],
        actions=[{"action": "payment", "human_decision": {"status": "approved"}}],
    )
    report = build_reliability_report(run, risk_assessments=[{"risk_score": 0.1, "confidence": 0.95}], root_cause=None)
    d = report.to_dict()["dimensions"]

    assert d["correctness"] == {"value": 1.0, "available": True, "source": "llm_judge evidence.correctness"}
    assert d["goal_completion"]["value"] == 1.0
    assert d["constraint_adherence"]["value"] == 1.0
    assert d["decision_consistency"]["value"] == 1.0
    assert d["tool_usage"]["value"] == 1.0
    assert d["behavioral_reliability"]["available"] is True
    assert report.risk == 0.1
    assert report.confidence == 0.95


def test_unavailable_dimensions_are_labeled_not_fabricated():
    run = _base_run(evaluations=[], decisions=[], actions=[])
    report = build_reliability_report(run, risk_assessments=[], root_cause=None)
    d = report.to_dict()["dimensions"]

    assert d["correctness"] == {"value": None, "available": False, "source": "no correctness signal available (no llm_judge, no applicable constraint evaluator)"}
    assert d["goal_completion"]["available"] is False
    assert d["constraint_adherence"]["available"] is False
    assert d["decision_consistency"]["available"] is False
    # Tool usage has a documented neutral default even with no actions.
    assert d["tool_usage"] == {"value": 1.0, "available": True, "source": "no actions recorded (neutral default — nothing failed)"}
    # Behavioral reliability still computes from whatever IS available (tool_usage only).
    assert d["behavioral_reliability"]["value"] == 1.0
    assert report.risk is None
    assert report.confidence is None


def test_constraint_adherence_falls_back_to_policy_findings_when_no_evaluator_ran():
    run = _base_run(
        evaluations=[],
        decisions=[
            {
                "outcome": "stop",
                "reason": "critical policy violation",
                "evidence": {"policy_findings": [{"rule": "max_cost", "violated": True}, {"rule": "other", "violated": False}]},
            }
        ],
    )
    report = build_reliability_report(run, risk_assessments=[], root_cause=None)
    assert report.constraint_adherence.value == 0.5
    assert report.constraint_adherence.available is True


def test_correctness_falls_back_to_constraint_adherence_when_no_llm_judge():
    run = _base_run(
        evaluations=[
            {"evaluator": "constraint_adherence", "passed": False, "score": 0.0, "label": "constraint_violated", "confidence": 1.0, "evidence": {}}
        ]
    )
    report = build_reliability_report(run, risk_assessments=[], root_cause=None)
    assert report.correctness.value == 0.0
    assert "constraint_adherence" in report.correctness.source


def test_decision_consistency_penalizes_retries_and_overrides():
    run = _base_run(
        decisions=[
            {"outcome": "retry", "reason": "transient", "evidence": {}},
            {"outcome": "continue", "reason": "ok", "evidence": {}},
            {"outcome": "stop", "reason": "llm judge override", "evidence": {}},
        ]
    )
    report = build_reliability_report(run, risk_assessments=[], root_cause=None)
    # 1.0 - retry(0.10) - override(0.25) = 0.65
    assert round(report.decision_consistency.value, 2) == 0.65


def test_tool_usage_penalizes_rejected_actions():
    run = _base_run(
        actions=[
            {"action": "payment", "human_decision": {"status": "approved"}},
            {"action": "refund", "human_decision": {"status": "rejected"}},
        ]
    )
    report = build_reliability_report(run, risk_assessments=[], root_cause=None)
    assert report.tool_usage.value == 0.5


def test_root_cause_summary_and_recovery_status_reflect_real_data():
    run = _base_run(status="rolled_back")
    root_cause = {"explanation": "max_budget disappeared during state transition S3"}
    report = build_reliability_report(run, risk_assessments=[], root_cause=root_cause)
    assert report.root_cause_summary == "max_budget disappeared during state transition S3"
    assert report.recovery_status == "ROLLBACK_PENDING_RECOVERY"


def test_recovered_run_reports_recovered_status():
    run = _base_run(status="continue", parent_run_id="original-run-id")
    report = build_reliability_report(run, risk_assessments=[], root_cause=None)
    assert report.recovery_status == "RECOVERED"
