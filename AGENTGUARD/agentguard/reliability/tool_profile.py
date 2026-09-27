"""Tool Reliability Profiles — Phase 4.

Deterministic, documented aggregation over a tool's accumulated
`ToolCallEvent` history (agentguard.call_tool(), see context.py) — never
a separately-stored, independently-staleable statistic. Recomputed fresh
every time from the raw events, same principle as Phase 3's Reliability
Report (agentguard/reliability/report.py): a number the engine cannot
compute honestly is reported as unavailable, never fabricated.
"""
from __future__ import annotations

from typing import Any

from ..models import ToolProfile

MIN_SAMPLE_SIZE = 5
"""Fewer than this many recorded calls and the tool's reliability is
reported INSUFFICIENT_DATA rather than a misleading rate."""

MIN_P95_SAMPLES = 20
"""A p95 latency over fewer than this many samples is not a meaningful
percentile — reported as None (not computed) below this count, even
once sample_count clears MIN_SAMPLE_SIZE."""

RELIABLE_THRESHOLD = 0.95
DEGRADED_THRESHOLD = 0.80
"""success_rate >= RELIABLE_THRESHOLD -> RELIABLE;
>= DEGRADED_THRESHOLD -> DEGRADED; below -> UNRELIABLE."""


def build_tool_profile(tool: str, events: list[dict[str, Any]]) -> ToolProfile:
    n = len(events)
    if n < MIN_SAMPLE_SIZE:
        return ToolProfile(
            tool=tool,
            sample_count=n,
            reliability="INSUFFICIENT_DATA",
            min_sample_size=MIN_SAMPLE_SIZE,
        )

    successes = sum(1 for e in events if e["outcome"] == "success")
    failures = sum(1 for e in events if e["outcome"] == "failure")
    timeouts = sum(1 for e in events if e["outcome"] == "timeout")
    retries = sum(1 for e in events if e.get("attempt", 1) > 1)
    duplicates = sum(1 for e in events if e.get("duplicate", False))

    success_rate = successes / n
    failure_rate = failures / n
    timeout_rate = timeouts / n
    retry_rate = retries / n
    duplicate_rate = duplicates / n

    latencies = sorted(e["latency_ms"] for e in events if e.get("latency_ms") is not None)
    latency_mean = sum(latencies) / len(latencies) if latencies else None
    latency_p95 = _percentile(latencies, 0.95) if len(latencies) >= MIN_P95_SAMPLES else None

    if success_rate >= RELIABLE_THRESHOLD:
        reliability = "RELIABLE"
    elif success_rate >= DEGRADED_THRESHOLD:
        reliability = "DEGRADED"
    else:
        reliability = "UNRELIABLE"

    return ToolProfile(
        tool=tool,
        sample_count=n,
        success_rate=success_rate,
        failure_rate=failure_rate,
        timeout_rate=timeout_rate,
        retry_rate=retry_rate,
        duplicate_call_rate=duplicate_rate,
        latency_mean_ms=latency_mean,
        latency_p95_ms=latency_p95,
        reliability=reliability,
        min_sample_size=MIN_SAMPLE_SIZE,
    )


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[idx]


class ToolProfileEngine:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def profile(self, tool: str) -> ToolProfile:
        events = await self._repository.list_tool_calls(tool)
        return build_tool_profile(tool, events)

    async def is_reliable(self, tool: str, *, threshold: float) -> bool:
        """True only when there is ENOUGH data to trust the number
        (Rule: never compute misleading statistics from too few
        samples) AND that number clears `threshold`."""
        p = await self.profile(tool)
        if p.reliability == "INSUFFICIENT_DATA" or p.success_rate is None:
            return False
        return p.success_rate >= threshold
