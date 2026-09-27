"""Checkpoint Engine — typed, serializable AgentState snapshots suitable
for rollback (Phase 3).

Every StateSnapshot recorded during a run (context.py's S1, S2, S3, ...
sequence, one per agentguard.update_state()/reset_state() call plus the
seeded initial state) becomes exactly one Checkpoint, persisted once the
run completes (decorator.py). A checkpoint never serializes arbitrary
Python runtime objects: `Checkpoint.state` is the same plain, JSON-safe
dict `StateSnapshot.data` already is (AgentState.snapshot() ->
pydantic's model_dump()), so there is nothing here that can fail to
round-trip through JSONB or through Checkpoint(**row) on the way back
out of storage.
"""
from __future__ import annotations

import hashlib

from ..audit.hashchain import canonical_json
from ..models import Checkpoint, StateSnapshot


def _state_hash(state: dict) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


class CheckpointEngine:
    def create(self, run_id: str, snapshot: StateSnapshot) -> Checkpoint:
        state = dict(snapshot.data)
        return Checkpoint(
            run_id=run_id,
            label=snapshot.label,
            seq=snapshot.seq,
            state_hash=_state_hash(state),
            state=state,
            valid=True,
        )

    def verify(self, checkpoint: Checkpoint) -> bool:
        """Recompute state_hash from checkpoint.state and compare —
        detects a stored checkpoint's state having been tampered with,
        independently of the run's audit hash chain. rollback() refuses
        to restore from a checkpoint that fails this check."""
        return _state_hash(checkpoint.state) == checkpoint.state_hash

    def select_safe_checkpoint(
        self, checkpoints: list[Checkpoint], *, before_label: str
    ) -> Checkpoint | None:
        """The checkpoint immediately before `before_label` (typically a
        RootCause.earliest_deviation, e.g. "S3") — the last known-good
        state before things went wrong. Returns None if no such
        checkpoint exists (e.g. the deviation was already at the first
        recorded state) or if the one immediately prior isn't valid, in
        which case the caller should walk further back."""
        ordered = sorted(checkpoints, key=lambda c: c.seq)
        target = next((c for c in ordered if c.label == before_label), None)
        if target is None:
            return ordered[-1] if ordered else None
        candidates = [c for c in ordered if c.seq < target.seq and c.valid]
        return candidates[-1] if candidates else None
