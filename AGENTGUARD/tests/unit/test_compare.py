from __future__ import annotations

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard.regression.compare import compare_metrics, compare_runs, mean_metrics

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository
    from agentguard.human.broker import reset_broker

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


def test_metric_delta_calculation_is_arithmetic_and_factual():
    result = compare_metrics({"constraint_adherence": 0.72}, {"constraint_adherence": 0.91}, label_a="v1", label_b="v2")
    delta = result.get("constraint_adherence")
    assert delta.value_a == 0.72
    assert delta.value_b == 0.91
    assert round(delta.delta, 2) == 0.19
    assert delta.available is True
    assert result.label_a == "v1"
    assert result.label_b == "v2"


def test_compare_never_declares_a_winner():
    """ComparisonResult carries no 'winner'/'better' field at all —
    structurally impossible to report a subjective verdict."""
    result = compare_metrics({"a": 1.0}, {"a": 0.5})
    for key in vars(result):
        assert "winner" not in key and "better" not in key
    for m in result.metrics:
        for key in vars(m):
            assert "winner" not in key and "better" not in key


def test_mixed_improvement_and_decline_both_shown():
    result = compare_metrics(
        {"constraint_adherence": 0.72, "goal_completion": 0.91},
        {"constraint_adherence": 0.91, "goal_completion": 0.87},
    )
    ca = result.get("constraint_adherence")
    gc = result.get("goal_completion")
    assert ca.delta > 0  # improved
    assert gc.delta < 0  # declined
    # Both rows are present regardless of direction.
    assert ca in result.metrics and gc in result.metrics


def test_unavailable_metric_on_either_side_is_never_defaulted_to_zero():
    result = compare_metrics({"goal_completion": None}, {"goal_completion": 0.9})
    delta = result.get("goal_completion")
    assert delta.available is False
    assert delta.delta is None
    assert delta.value_a is None
    assert delta.value_b == 0.9


def test_mean_metrics_averages_only_available_values():
    aggregated = mean_metrics([{"risk": 0.2}, {"risk": 0.4}, {"risk": None}])
    assert aggregated["risk"] == pytest.approx(0.3)


def test_mean_metrics_reports_none_when_no_run_has_the_metric():
    aggregated = mean_metrics([{"risk": None}, {"risk": None}])
    assert aggregated["risk"] is None


async def test_six_dimension_comparison_between_two_real_runs(fake_repository):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def bad_agent(task: str) -> str:
        agentguard.update_state(max_budget=67000)
        return "over"

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def good_agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    await bad_agent("task")
    await good_agent("task")
    run_ids = list(fake_repository.runs.keys())

    result = await compare_runs(fake_repository, run_ids[0], run_ids[1])
    metric_names = {m.metric for m in result.metrics}
    assert {
        "correctness",
        "goal_completion",
        "constraint_adherence",
        "decision_consistency",
        "tool_usage",
        "behavioral_reliability",
        "risk",
        "confidence",
        "retry_count",
        "replan_count",
        "human_intervention_count",
        "failure_count",
        "latency_ms",
    }.issubset(metric_names)

    ca = result.get("constraint_adherence")
    assert ca.value_a == 0.0
    assert ca.value_b == 1.0
    assert ca.delta == 1.0

    failure = result.get("failure_count")
    assert failure.value_a == 1.0
    assert failure.value_b == 0.0


async def test_compare_runs_raises_for_unknown_run(fake_repository):
    with pytest.raises(ValueError):
        await compare_runs(fake_repository, "does-not-exist", "also-missing")
