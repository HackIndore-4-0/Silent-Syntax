"""Confidence / uncertainty classification.

Fixed, documented thresholds — used by the Decision Engine to implement
the Phase 2 routing table exactly:

    HIGH confidence + SAFE   -> CONTINUE
    HIGH confidence + UNSAFE -> STOP
    LOW  confidence + HIGH impact -> HUMAN
    LOW  confidence + LOW impact  -> policy.default_on_uncertain

Never silently convert uncertainty into safety: a LOW confidence result
never falls through to CONTINUE by default — it always routes to HUMAN
or the policy's explicit fallback.
"""
from __future__ import annotations

from typing import Literal

CONFIDENCE_THRESHOLD = 0.7
"""confidence >= this is HIGH; below it is LOW."""

IMPACT_THRESHOLD = 0.6
"""impact >= this is HIGH; below it is LOW."""

Level = Literal["high", "low"]


def confidence_level(confidence: float) -> Level:
    return "high" if confidence >= CONFIDENCE_THRESHOLD else "low"


def impact_level(impact: float) -> Level:
    return "high" if impact >= IMPACT_THRESHOLD else "low"
