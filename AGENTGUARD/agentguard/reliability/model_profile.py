"""Model Reliability Profiles — the LLM Gateway's reliability signal.

Deterministic, documented aggregation over a model's accumulated
TraceStep(kind="llm_call") history — never a separately-stored,
independently-staleable statistic. Recomputed fresh every time from the
raw events, same principle as tool_profile.py/Phase 3's Reliability
Report: a number the engine cannot compute honestly is reported as
unavailable, never fabricated.

Mirrors tool_profile.py's thresholds verbatim (no reason to diverge
from an already-battle-tested classification scheme). TraceStep has no
"attempt"/"duplicate" fields (unlike ToolCallEvent), so retry_rate and
duplicate_call_rate are always None here — "not tracked" stays distinct
from "measured as zero". timeout_rate is likewise always None: TraceStep's
outcome is only "success"/"failure", with no separate "timeout" value.
"""
from __future__ import annotations

from typing import Any

from ..models import ModelProfile

MIN_SAMPLE_SIZE = 5
MIN_P95_SAMPLES = 20
RELIABLE_THRESHOLD = 0.95
DEGRADED_THRESHOLD = 0.80


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_model_profile(model: str, events: list[dict[str, Any]]) -> ModelProfile:
    n = len(events)
    if n < MIN_SAMPLE_SIZE:
        return ModelProfile(
            model=model,
            sample_count=n,
            reliability="INSUFFICIENT_DATA",
            min_sample_size=MIN_SAMPLE_SIZE,
        )

    successes = sum(1 for e in events if e["outcome"] == "success")
    failures = sum(1 for e in events if e["outcome"] == "failure")

    success_rate = successes / n
    failure_rate = failures / n

    latencies = sorted(e["latency_ms"] for e in events if e.get("latency_ms") is not None)
    latency_mean = _mean(latencies)
    latency_p95 = _percentile(latencies, 0.95) if len(latencies) >= MIN_P95_SAMPLES else None

    tokens_input = [e["tokens_input"] for e in events if e.get("tokens_input") is not None]
    tokens_output = [e["tokens_output"] for e in events if e.get("tokens_output") is not None]
    costs = [e["cost_usd"] for e in events if e.get("cost_usd") is not None]

    if success_rate >= RELIABLE_THRESHOLD:
        reliability = "RELIABLE"
    elif success_rate >= DEGRADED_THRESHOLD:
        reliability = "DEGRADED"
    else:
        reliability = "UNRELIABLE"

    return ModelProfile(
        model=model,
        sample_count=n,
        success_rate=success_rate,
        failure_rate=failure_rate,
        timeout_rate=None,
        retry_rate=None,
        duplicate_call_rate=None,
        latency_mean_ms=latency_mean,
        latency_p95_ms=latency_p95,
        avg_tokens_input=_mean(tokens_input),
        avg_tokens_output=_mean(tokens_output),
        avg_cost_usd=_mean(costs),
        reliability=reliability,
        min_sample_size=MIN_SAMPLE_SIZE,
    )


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[idx]


class ModelProfileEngine:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def profile(self, model: str, *, workspace_id: str | None = None) -> ModelProfile:
        events = await self._repository.list_llm_calls(model, workspace_id=workspace_id)
        return build_model_profile(model, events)

    async def is_reliable(self, model: str, *, threshold: float, workspace_id: str | None = None) -> bool:
        """True only when there is ENOUGH data to trust the number AND
        that number clears `threshold` — never a guess from too few
        samples (same rule as ToolProfileEngine.is_reliable)."""
        p = await self.profile(model, workspace_id=workspace_id)
        if p.reliability == "INSUFFICIENT_DATA" or p.success_rate is None:
            return False
        return p.success_rate >= threshold
