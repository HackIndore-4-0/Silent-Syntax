"""The "defined recovery/replan mechanism" a rollback hands the *next*
@monitor-wrapped invocation of an agent function, without decorator.py
needing to know anything about checkpoints or rollback itself.

Usage (see examples/budget_failure_recovery.py):

    result = await agentguard.recovery.rollback(repository, run_id, "S2")
    agentguard.recovery.seed_recovery_state(
        result.restored_state, parent_run_id=run_id, checkpoint_id=result.checkpoint.id,
    )
    recovered = await safe_agent("retry the task")   # a normal @monitor call

This is a one-shot contextvar: decorator.py pops it the moment the next
monitored function starts executing, so it never leaks into an unrelated
later run, and an ordinary (non-recovery) call is completely unaffected
(the seed is None, decorator.py falls back to its Phase 1/2 initial-state
logic exactly as before).
"""
from __future__ import annotations

import contextvars
from typing import Any, TypedDict


class RecoverySeed(TypedDict):
    state: dict[str, Any]
    parent_run_id: str | None
    checkpoint_id: str | None


_seed: "contextvars.ContextVar[RecoverySeed | None]" = contextvars.ContextVar(
    "agentguard_recovery_seed", default=None
)


def seed_recovery_state(
    state: dict[str, Any], *, parent_run_id: str | None = None, checkpoint_id: str | None = None
) -> None:
    _seed.set({"state": dict(state), "parent_run_id": parent_run_id, "checkpoint_id": checkpoint_id})


def pop_recovery_seed() -> "RecoverySeed | None":
    seed = _seed.get()
    if seed is not None:
        _seed.set(None)
    return seed
