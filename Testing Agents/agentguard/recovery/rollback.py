"""Rollback — restore a run's state to an earlier, valid checkpoint.

`rollback(repository, run_id, to_checkpoint)` is deterministic and
side-effect-explicit: every state restoration is paired with (a) a
ROLLBACK AuditEvent appended to the run's existing hash chain and (b) a
Decision recorded through a real DecisionEngine instance — never a bare
mutation of run.status (see RunStatus.ROLLED_BACK in agentguard/models.py
and DecisionEngine.rollback in decision/engine.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..audit.chain import append_event
from ..checkpoint.engine import CheckpointEngine
from ..decision.engine import DecisionEngine
from ..models import Checkpoint, Decision, Policy, Run, RunStatus
from ..storage.repository import RunRepository


class RollbackError(ValueError):
    """Raised for any invalid rollback request — unknown run, unknown or
    foreign checkpoint, an invalid/tampered checkpoint, or a run that
    hasn't actually finished executing yet. Never a silent no-op."""


@dataclass
class RollbackResult:
    run_id: str
    checkpoint: Checkpoint
    previous_state: dict[str, Any]
    restored_state: dict[str, Any]
    decision: Decision


def _run_from_dict(data: dict[str, Any]) -> Run:
    policy_data = data.get("policy") or {}
    policy = Policy(**{k: v for k, v in policy_data.items() if k in Policy.model_fields})
    run_fields = {k: v for k, v in data.items() if k in Run.model_fields and k != "policy"}
    return Run(policy=policy, **run_fields)


async def rollback(repository: RunRepository, run_id: str, to_checkpoint: str) -> RollbackResult:
    """`to_checkpoint` is either a Checkpoint.id or its label (e.g. "S2")
    — whichever the caller has on hand.

    Steps (per the Phase 3 spec):
        1. verify checkpoint belongs to run
        2. verify checkpoint is valid (status flag + hash re-verification)
        3. restore AgentState (return restored_state to the caller)
        4. record a ROLLBACK audit event
        5. record previous_state and restored_state on that event
        6. mark the rollback decision in the Decision Engine
        7. the caller then hands restored_state to
           agentguard.recovery.seed_recovery_state() to actually continue
           execution — this function only performs the rollback itself.
    """
    run_data = await repository.get_run(run_id)
    if run_data is None:
        raise RollbackError(f"run {run_id!r} not found")

    current_status = run_data.get("status")
    if current_status in (RunStatus.RUNNING.value, RunStatus.EVALUATING.value, RunStatus.RETRY.value, RunStatus.REPLAN.value):
        raise RollbackError(f"run {run_id!r} has not finished executing yet (status={current_status!r})")

    checkpoints = await repository.list_checkpoints(run_id)
    matches = [c for c in checkpoints if c["id"] == to_checkpoint or c["label"] == to_checkpoint]
    if not matches:
        raise RollbackError(f"checkpoint {to_checkpoint!r} not found for run {run_id!r}")
    checkpoint = Checkpoint(**matches[0])

    if checkpoint.run_id != run_id:
        raise RollbackError(f"checkpoint {checkpoint.id!r} does not belong to run {run_id!r}")
    if not checkpoint.valid:
        raise RollbackError(f"checkpoint {checkpoint.id!r} is marked invalid")
    if not CheckpointEngine().verify(checkpoint):
        raise RollbackError(
            f"checkpoint {checkpoint.id!r} failed state-hash verification — refusing to roll back to a tampered checkpoint"
        )

    previous_state = run_data.get("final_state") or {}
    restored_state = dict(checkpoint.state)

    engine = DecisionEngine()
    engine.state = RunStatus(current_status)
    decision = engine.rollback(
        reason=f"rolled back to checkpoint {checkpoint.label!r} ({checkpoint.id})",
        evidence={
            "checkpoint_id": checkpoint.id,
            "checkpoint_label": checkpoint.label,
            "previous_state": previous_state,
            "restored_state": restored_state,
        },
    )

    policy_version = (run_data.get("policy") or {}).get("version")
    await append_event(
        repository,
        run_id,
        "ROLLBACK",
        {
            "checkpoint_id": checkpoint.id,
            "checkpoint_label": checkpoint.label,
            "previous_state": previous_state,
            "restored_state": restored_state,
            "reason": decision.reason,
        },
        policy_version=policy_version,
    )

    await repository.set_run_status(run_id, RunStatus.ROLLED_BACK)
    run_for_decision = _run_from_dict(run_data)
    run_for_decision.status = RunStatus.ROLLED_BACK
    await repository.save_decision(run_for_decision, decision)

    return RollbackResult(
        run_id=run_id,
        checkpoint=checkpoint,
        previous_state=previous_state,
        restored_state=restored_state,
        decision=decision,
    )
