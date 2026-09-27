from __future__ import annotations

from agentguard.models import ToolCallEvent
from agentguard.reliability.tool_profile import MIN_SAMPLE_SIZE, build_tool_profile


def _event(outcome="success", attempt=1, duplicate=False, latency_ms=100.0) -> dict:
    return ToolCallEvent(
        run_id="run-1", tool="search_api", attempt=attempt, outcome=outcome, duplicate=duplicate, latency_ms=latency_ms
    ).model_dump()


def test_profile_creation_from_events():
    events = [_event() for _ in range(6)]
    profile = build_tool_profile("search_api", events)
    assert profile.tool == "search_api"
    assert profile.sample_count == 6


def test_sample_count_reported_accurately():
    events = [_event() for _ in range(3)]
    profile = build_tool_profile("search_api", events)
    assert profile.sample_count == 3


def test_success_rate_computed_correctly():
    events = [_event("success")] * 8 + [_event("failure")] * 2
    profile = build_tool_profile("search_api", events)
    assert profile.success_rate == 0.8
    assert profile.failure_rate == 0.2
    assert profile.reliability == "DEGRADED"  # >= 0.80 threshold


def test_timeout_rate_computed_correctly():
    events = [_event("success")] * 7 + [_event("timeout")] * 3
    profile = build_tool_profile("search_api", events)
    assert profile.timeout_rate == 0.3
    assert profile.failure_rate == 0.0


def test_retry_and_duplicate_rate():
    events = [
        _event(attempt=1),
        _event(attempt=2),
        _event(attempt=1, duplicate=True),
        _event(attempt=1),
        _event(attempt=1),
    ]
    profile = build_tool_profile("search_api", events)
    assert profile.retry_rate == 0.2
    assert profile.duplicate_call_rate == 0.2


def test_insufficient_data_below_min_sample_size():
    events = [_event() for _ in range(MIN_SAMPLE_SIZE - 1)]
    profile = build_tool_profile("search_api", events)
    assert profile.reliability == "INSUFFICIENT_DATA"
    assert profile.success_rate is None
    assert profile.sample_count == MIN_SAMPLE_SIZE - 1


def test_reliable_and_unreliable_thresholds():
    reliable = build_tool_profile("t", [_event("success")] * 20)
    assert reliable.reliability == "RELIABLE"

    unreliable = build_tool_profile("t", [_event("success")] * 3 + [_event("failure")] * 7)
    assert unreliable.reliability == "UNRELIABLE"


def test_p95_latency_requires_enough_samples():
    few = [_event(latency_ms=float(i)) for i in range(10)]
    profile_few = build_tool_profile("t", few)
    assert profile_few.latency_p95_ms is None  # < MIN_P95_SAMPLES
    assert profile_few.latency_mean_ms is not None

    many = [_event(latency_ms=float(i)) for i in range(25)]
    profile_many = build_tool_profile("t", many)
    assert profile_many.latency_p95_ms is not None
