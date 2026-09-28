"""Confidence label for a stored model Recommendation.

Deliberately reuses reliability/model_profile.py's own
MIN_SAMPLE_SIZE/MIN_P95_SAMPLES thresholds verbatim rather than inventing
a second, competing notion of "enough data" — a recommendation backed by
N pieces of evidence should be judged by the same bar this codebase
already uses everywhere else to decide when a sample size is trustworthy.

Computed fresh from evidence_ids on every read (never stored), so it can
never drift from the evidence it describes.
"""
from __future__ import annotations

from typing import Literal

from ..reliability.model_profile import MIN_P95_SAMPLES, MIN_SAMPLE_SIZE

RecommendationConfidence = Literal["INSUFFICIENT_DATA", "MODERATE", "HIGH"]


def compute_recommendation_confidence(evidence_count: int) -> RecommendationConfidence:
    if evidence_count < MIN_SAMPLE_SIZE:
        return "INSUFFICIENT_DATA"
    if evidence_count < MIN_P95_SAMPLES:
        return "MODERATE"
    return "HIGH"
