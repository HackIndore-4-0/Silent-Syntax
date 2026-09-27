"""Audit trail construction and verification.

Used two ways:

1. In-process, synchronous chain building during a single run.
   `decorator.py` accumulates `(event_type, payload)` pairs into
   `RunContext.audit_log` as the run executes (RUN START, AGENT STEP,
   POLICY CHECK, EVALUATION, RISK ASSESSMENT, ROOT CAUSE, DECISION,
   CHECKPOINT, HUMAN, RUN COMPLETION), then `build_chain` turns that
   ordered list into a real SHA-256 hash chain and the caller persists it
   in ONE batch at the very end of the run. This keeps per-event hash
   chaining off the database round-trip critical path — one batch write
   per run for the whole chain, not one write per event.

2. A single later append — e.g. a ROLLBACK event recorded against an
   already-completed run, minutes or hours after RUN COMPLETION — via
   `append_event`, which loads the run's last persisted event first
   (there is no in-memory chain state left to build on at that point).
"""
from __future__ import annotations

from typing import Any, Protocol

from ..models import AuditEvent
from .hashchain import GENESIS_HASH, compute_event_hash


def build_chain(
    run_id: str,
    events: list[tuple[str, dict[str, Any]]],
    *,
    policy_version: int | None,
    start_seq: int = 1,
    previous_hash: str = GENESIS_HASH,
) -> list[AuditEvent]:
    """Turn an ordered list of (event_type, payload) pairs into a real
    hash chain, starting from `previous_hash` (GENESIS_HASH for a brand
    new run_id, or the run's last persisted event_hash when extending an
    existing chain)."""
    chain: list[AuditEvent] = []
    current_previous = previous_hash
    for i, (event_type, payload) in enumerate(events):
        seq = start_seq + i
        event_hash = compute_event_hash(payload, current_previous)
        chain.append(
            AuditEvent(
                run_id=run_id,
                seq=seq,
                event_type=event_type,
                payload=payload,
                previous_hash=current_previous,
                event_hash=event_hash,
                policy_version=policy_version,
            )
        )
        current_previous = event_hash
    return chain


class _AuditRepository(Protocol):
    async def list_audit_events(self, run_id: str) -> list[dict[str, Any]]: ...
    async def save_audit_event(self, event: AuditEvent) -> None: ...


async def append_event(
    repository: _AuditRepository,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    policy_version: int | None,
) -> AuditEvent:
    """Append a single event to a run's existing (possibly empty) chain,
    loading the current chain tail from the repository first. Used for
    events recorded outside the original run's own execution (e.g.
    ROLLBACK)."""
    existing = await repository.list_audit_events(run_id)
    previous_hash = existing[-1]["event_hash"] if existing else GENESIS_HASH
    seq = (existing[-1]["seq"] if existing else 0) + 1
    event_hash = compute_event_hash(payload, previous_hash)
    event = AuditEvent(
        run_id=run_id,
        seq=seq,
        event_type=event_type,
        payload=payload,
        previous_hash=previous_hash,
        event_hash=event_hash,
        policy_version=policy_version,
    )
    await repository.save_audit_event(event)
    return event


async def verify_audit_chain(repository: _AuditRepository, run_id: str) -> dict[str, Any]:
    """Recompute every event's hash in order and detect the first broken
    link — either a payload that no longer hashes to its stored
    event_hash, or a previous_hash that no longer points at the prior
    event's real hash (catches a deleted/reordered event too).

    Returns exactly the shape the spec asks for:
        intact, first_invalid_event, expected_hash, actual_hash
    plus event_count for reporting.
    """
    events = await repository.list_audit_events(run_id)
    previous_hash = GENESIS_HASH
    for event in events:
        expected = compute_event_hash(event["payload"], previous_hash)
        if event["previous_hash"] != previous_hash or event["event_hash"] != expected:
            return {
                "run_id": run_id,
                "intact": False,
                "event_count": len(events),
                "first_invalid_event": event["id"],
                "first_invalid_seq": event["seq"],
                "expected_hash": expected,
                "actual_hash": event["event_hash"],
            }
        previous_hash = event["event_hash"]
    return {
        "run_id": run_id,
        "intact": True,
        "event_count": len(events),
        "first_invalid_event": None,
        "first_invalid_seq": None,
        "expected_hash": None,
        "actual_hash": None,
    }
