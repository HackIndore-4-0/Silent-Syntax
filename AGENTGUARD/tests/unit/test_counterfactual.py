from __future__ import annotations

from agentguard.recovery.counterfactual import generate_counterfactual


def _run(status: str, final_state: dict) -> dict:
    return {"id": "run-1", "status": status, "final_state": final_state}


def _checkpoints(*pairs: tuple[str, int, dict]) -> list[dict]:
    return [{"label": label, "seq": seq, "state": state} for label, seq, state in pairs]


def test_counterfactual_generation_produces_labeled_paths():
    original = _run("stop", {"max_budget": 67000})
    original_checkpoints = _checkpoints(
        ("S1", 1, {"max_budget": 60000}),
        ("S2", 2, {"max_budget": 60000}),
        ("S3", 3, {}),
        ("S4", 4, {"max_budget": 67000}),
    )
    recovery = _run("continue", {"max_budget": 52000})
    recovery_checkpoints = _checkpoints(("S1", 1, {"max_budget": 60000}), ("S2", 2, {"max_budget": 52000}))
    root_cause = {
        "earliest_deviation": "S3",
        "expected": {"max_budget": 60000},
        "confidence": 0.97,
    }

    cf = generate_counterfactual(original, original_checkpoints, recovery, recovery_checkpoints, "cp-2", root_cause)

    assert cf.run_id == "run-1"
    assert cf.source_checkpoint_id == "cp-2"
    assert [s["label"] for s in cf.actual_path] == ["S1", "S2", "S3", "S4", "result"]
    assert [s["label"] for s in cf.counterfactual_path] == ["S1", "S2", "result"]
    assert cf.confidence == 0.97
    assert cf.altered_state["diverged_at"] == "S3"


def test_actual_and_counterfactual_paths_are_clearly_distinguished():
    original = _run("stop", {"max_budget": 67000})
    recovery = _run("continue", {"max_budget": 52000})
    cf = generate_counterfactual(original, _checkpoints(("S1", 1, {})), recovery, _checkpoints(("S1", 1, {})), "cp-1", None)

    actual_result = cf.actual_path[-1]
    counterfactual_result = cf.counterfactual_path[-1]
    assert actual_result["status"] == "STOP"
    assert counterfactual_result["status"] == "ALLOWED"
    assert actual_result["data"] != counterfactual_result["data"]
    assert "ACTUAL" in cf.result and "COUNTERFACTUAL" in cf.result


def test_counterfactual_without_root_cause_uses_neutral_confidence():
    original = _run("stop", {"x": 1})
    recovery = _run("continue", {"x": 2})
    cf = generate_counterfactual(original, [], recovery, [], "cp-1", None)
    assert cf.confidence == 0.5
    assert cf.altered_state["diverged_at"] is None


def test_canonical_budget_scenario_matches_the_spec_example():
    """S1/S2: max_budget=60000 (both paths). S3: lost in the actual path,
    retained in the counterfactual. S4/result: 67000 (STOP) vs. 52000
    (ALLOWED) — exactly the Final Solution spec's example."""
    original = _run("stop", {"max_budget": 67000})
    original_checkpoints = _checkpoints(
        ("S1", 1, {"max_budget": 60000}),
        ("S2", 2, {"max_budget": 60000}),
        ("S3", 3, {}),
        ("S4", 4, {"max_budget": 67000}),
    )
    recovery = _run("continue", {"max_budget": 52000})
    recovery_checkpoints = _checkpoints(("S1", 1, {"max_budget": 60000}), ("S2", 2, {"max_budget": 52000}))
    root_cause = {"earliest_deviation": "S3", "expected": {"max_budget": 60000}, "confidence": 0.97}

    cf = generate_counterfactual(original, original_checkpoints, recovery, recovery_checkpoints, "cp-2", root_cause)

    actual_states = {s["label"]: s["data"] for s in cf.actual_path}
    counterfactual_states = {s["label"]: s["data"] for s in cf.counterfactual_path}

    assert actual_states["S1"]["max_budget"] == 60000
    assert actual_states["S2"]["max_budget"] == 60000
    assert actual_states["S3"] == {}
    assert actual_states["S4"]["max_budget"] == 67000
    assert cf.actual_path[-1]["status"] == "STOP"

    assert counterfactual_states["S1"]["max_budget"] == 60000
    assert counterfactual_states["S2"]["max_budget"] == 52000
    assert cf.counterfactual_path[-1]["status"] == "ALLOWED"
