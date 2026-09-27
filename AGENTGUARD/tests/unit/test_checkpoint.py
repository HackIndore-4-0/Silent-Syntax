from __future__ import annotations

from agentguard.checkpoint.engine import CheckpointEngine
from agentguard.models import StateSnapshot


def test_checkpoint_creation_from_snapshot():
    engine = CheckpointEngine()
    snapshot = StateSnapshot(label="S2", seq=2, data={"max_budget": 60000})
    checkpoint = engine.create("run-1", snapshot)

    assert checkpoint.run_id == "run-1"
    assert checkpoint.label == "S2"
    assert checkpoint.seq == 2
    assert checkpoint.state == {"max_budget": 60000}
    assert checkpoint.valid is True


def test_checkpoint_state_hashing_is_deterministic_and_content_addressed():
    engine = CheckpointEngine()
    a = engine.create("run-1", StateSnapshot(label="S1", seq=1, data={"max_budget": 60000, "x": 1}))
    b = engine.create("run-1", StateSnapshot(label="S1", seq=1, data={"x": 1, "max_budget": 60000}))
    c = engine.create("run-1", StateSnapshot(label="S1", seq=1, data={"max_budget": 60001, "x": 1}))

    # Same logical state (different key insertion order) -> same hash.
    assert a.state_hash == b.state_hash
    # Different state -> different hash.
    assert a.state_hash != c.state_hash
    assert len(a.state_hash) == 64  # SHA-256 hex digest


def test_valid_checkpoint_passes_verification():
    engine = CheckpointEngine()
    checkpoint = engine.create("run-1", StateSnapshot(label="S1", seq=1, data={"max_budget": 60000}))
    assert engine.verify(checkpoint) is True


def test_tampered_checkpoint_fails_verification():
    engine = CheckpointEngine()
    checkpoint = engine.create("run-1", StateSnapshot(label="S1", seq=1, data={"max_budget": 60000}))
    checkpoint.state["max_budget"] = 999999  # tamper with the stored state directly
    assert engine.verify(checkpoint) is False


def test_select_safe_checkpoint_picks_the_one_immediately_before_the_deviation():
    engine = CheckpointEngine()
    checkpoints = [
        engine.create("run-1", StateSnapshot(label="S1", seq=1, data={"max_budget": 60000})),
        engine.create("run-1", StateSnapshot(label="S2", seq=2, data={"max_budget": 60000})),
        engine.create("run-1", StateSnapshot(label="S3", seq=3, data={})),
        engine.create("run-1", StateSnapshot(label="S4", seq=4, data={"max_budget": 67000})),
    ]

    safe = engine.select_safe_checkpoint(checkpoints, before_label="S3")
    assert safe is not None
    assert safe.label == "S2"


def test_select_safe_checkpoint_skips_invalid_checkpoints():
    engine = CheckpointEngine()
    s1 = engine.create("run-1", StateSnapshot(label="S1", seq=1, data={"max_budget": 60000}))
    s2 = engine.create("run-1", StateSnapshot(label="S2", seq=2, data={"max_budget": 60000}))
    s2.valid = False
    s3 = engine.create("run-1", StateSnapshot(label="S3", seq=3, data={}))

    safe = engine.select_safe_checkpoint([s1, s2, s3], before_label="S3")
    assert safe is not None
    assert safe.label == "S1"
