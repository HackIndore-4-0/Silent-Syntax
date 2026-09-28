"""compute_recommendation_confidence — boundary checks around the exact
thresholds reused verbatim from reliability/model_profile.py, so a
change to those thresholds is caught here too instead of silently
drifting the two apart.
"""
from __future__ import annotations

from agentguard.evaluation.recommendation_confidence import compute_recommendation_confidence
from agentguard.reliability.model_profile import MIN_P95_SAMPLES, MIN_SAMPLE_SIZE


def test_zero_evidence_is_insufficient_data():
    assert compute_recommendation_confidence(0) == "INSUFFICIENT_DATA"


def test_just_below_min_sample_size_is_insufficient_data():
    assert compute_recommendation_confidence(MIN_SAMPLE_SIZE - 1) == "INSUFFICIENT_DATA"


def test_at_min_sample_size_is_moderate():
    assert compute_recommendation_confidence(MIN_SAMPLE_SIZE) == "MODERATE"


def test_just_below_min_p95_samples_is_moderate():
    assert compute_recommendation_confidence(MIN_P95_SAMPLES - 1) == "MODERATE"


def test_at_min_p95_samples_is_high():
    assert compute_recommendation_confidence(MIN_P95_SAMPLES) == "HIGH"


def test_well_above_min_p95_samples_is_high():
    assert compute_recommendation_confidence(MIN_P95_SAMPLES * 10) == "HIGH"
