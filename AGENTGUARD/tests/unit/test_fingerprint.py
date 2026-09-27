from __future__ import annotations

from agentguard.reliability.fingerprint import MIN_SAMPLE_SIZE, build_behavior_fingerprint


def _run(retry_count=0, duration_ms=1000.0, status="continue", decisions=None) -> dict:
    return {"retry_count": retry_count, "duration_ms": duration_ms, "status": status, "decisions": decisions or []}


def test_insufficient_history_returns_no_baseline():
    fp = build_behavior_fingerprint("agent", historical_runs=[_run()] * 3, historical_tool_call_counts=[7, 7, 7])
    assert fp.sample_count == 3
    assert fp.baseline is None
    assert fp.current is None
    assert fp.anomaly is False
    assert fp.min_sample_size == MIN_SAMPLE_SIZE


def test_baseline_creation_with_enough_history():
    runs = [_run(retry_count=1, duration_ms=2800.0) for _ in range(5)]
    fp = build_behavior_fingerprint("agent", historical_runs=runs, historical_tool_call_counts=[7] * 5)
    assert fp.sample_count == 5
    assert fp.baseline["tool_calls"] == 7.0
    assert fp.baseline["retry_count"] == 1.0
    assert fp.baseline["latency_ms"] == 2800.0
    assert fp.baseline["completion_rate"] == 1.0
    assert fp.baseline["failure_rate"] == 0.0


def test_anomaly_detection_matches_canonical_example():
    """Baseline: tool_calls=7.2, retry_rate=0.8, p95_latency=2.8s.
    Current: tool_calls=19, retry_count=8, latency=7.8s -> anomaly."""
    runs = [_run(retry_count=1, duration_ms=2800.0) for _ in range(5)]
    tool_counts = [7, 7, 8, 7, 7]  # mean 7.2

    current_run = _run(retry_count=8, duration_ms=7800.0)
    fp = build_behavior_fingerprint(
        "agent",
        historical_runs=runs,
        historical_tool_call_counts=tool_counts,
        current_run=current_run,
        current_tool_call_count=19,
    )
    assert fp.sample_count == 5
    assert round(fp.baseline["tool_calls"], 1) == 7.2
    assert fp.current["tool_calls"] == 19.0
    assert fp.deviation["tool_calls"] > fp.anomaly_threshold
    assert fp.deviation["retry_count"] > fp.anomaly_threshold
    assert fp.deviation["latency_ms"] > fp.anomaly_threshold
    assert fp.anomaly is True


def test_normal_current_run_is_not_flagged():
    runs = [_run(retry_count=1, duration_ms=2800.0) for _ in range(5)]
    current_run = _run(retry_count=1, duration_ms=2900.0)
    fp = build_behavior_fingerprint(
        "agent", historical_runs=runs, historical_tool_call_counts=[7] * 5, current_run=current_run, current_tool_call_count=7
    )
    assert fp.anomaly is False


def test_human_intervention_frequency_and_failure_rate():
    runs = [
        _run(status="continue"),
        _run(status="continue"),
        _run(status="stop"),
        _run(status="continue", decisions=[{"outcome": "human"}]),
        _run(status="continue"),
    ]
    fp = build_behavior_fingerprint("agent", historical_runs=runs, historical_tool_call_counts=[1] * 5)
    assert fp.baseline["failure_rate"] == 0.2
    assert fp.baseline["human_intervention_frequency"] == 0.2


def test_zero_baseline_with_nonzero_current_is_treated_as_full_deviation():
    runs = [_run(retry_count=0) for _ in range(5)]
    current_run = _run(retry_count=3)
    fp = build_behavior_fingerprint(
        "agent", historical_runs=runs, historical_tool_call_counts=[0] * 5, current_run=current_run, current_tool_call_count=0
    )
    assert fp.deviation["retry_count"] == 1.0
    assert fp.anomaly is True
