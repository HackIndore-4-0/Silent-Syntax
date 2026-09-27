"""Counterfactual Engine — reconstructs "what would have happened if the
corrupted state had not occurred", grounded in a real, separately
executed recovery run rather than an invented number.

IMPORTANT: this is a reconstructed alternate execution, not a claim
about a literal historical event. The recovery run referenced here is a
genuine, independent execution (its own Run row, its own audit trail, a
later wall-clock time) that started from the checkpoint's retained state
— see agentguard/recovery/rollback.py, agentguard/recovery/seed.py, and
examples/budget_failure_recovery.py, which wires the two together. This
engine's only job is to package the *comparison* between the original
run's actual path and the recovery run's path into one auditable record;
it never fabricates a value the recovery run didn't actually produce.
"""
from __future__ import annotations

from typing import Any

from ..models import CounterfactualResult

_STATUS_LABEL = {
    "continue": "ALLOWED",
    "stop": "STOP",
    "failed": "FAILED",
    "human": "PENDING_HUMAN_REVIEW",
    "rolled_back": "ROLLED_BACK",
}


def _display_status(status: str | None) -> str:
    return _STATUS_LABEL.get(status or "", (status or "unknown").upper())


def _path_from_checkpoints(checkpoints: list[dict[str, Any]], *, final_state: dict, final_status: str) -> list[dict[str, Any]]:
    ordered = sorted(checkpoints, key=lambda c: c["seq"])
    path = [{"label": c["label"], "data": c["state"]} for c in ordered]
    path.append({"label": "result", "data": final_state, "status": _display_status(final_status)})
    return path


def generate_counterfactual(
    original_run: dict[str, Any],
    original_checkpoints: list[dict[str, Any]],
    recovery_run: dict[str, Any],
    recovery_checkpoints: list[dict[str, Any]],
    checkpoint_id: str,
    root_cause: dict[str, Any] | None,
) -> CounterfactualResult:
    actual_path = _path_from_checkpoints(
        original_checkpoints,
        final_state=original_run.get("final_state") or {},
        final_status=original_run.get("status"),
    )
    counterfactual_path = _path_from_checkpoints(
        recovery_checkpoints,
        final_state=recovery_run.get("final_state") or {},
        final_status=recovery_run.get("status"),
    )

    comparison = {
        "actual_status": original_run.get("status"),
        "actual_status_display": _display_status(original_run.get("status")),
        "counterfactual_status": recovery_run.get("status"),
        "counterfactual_status_display": _display_status(recovery_run.get("status")),
        "actual_final_state": original_run.get("final_state") or {},
        "counterfactual_final_state": recovery_run.get("final_state") or {},
        "retained_constraint": root_cause.get("expected") if root_cause else None,
    }

    result = (
        f"ACTUAL: {_display_status(original_run.get('status'))} "
        f"({original_run.get('final_state')})  vs.  "
        f"COUNTERFACTUAL: {_display_status(recovery_run.get('status'))} "
        f"({recovery_run.get('final_state')})"
    )

    # Confidence mirrors the root cause's own confidence: the
    # counterfactual is only as trustworthy as the root-cause attribution
    # it is built to negate. Without a root cause, confidence reflects
    # only "a real recovery run really executed and is being compared" —
    # a bare fact comparison, no causal claim — documented at 0.5.
    confidence = root_cause.get("confidence", 0.5) if root_cause else 0.5

    return CounterfactualResult(
        run_id=original_run["id"],
        source_checkpoint_id=checkpoint_id,
        actual_path=actual_path,
        counterfactual_path=counterfactual_path,
        altered_state={
            "diverged_at": root_cause.get("earliest_deviation") if root_cause else None,
            "retained": root_cause.get("expected", {}) if root_cause else {},
        },
        result=result,
        comparison=comparison,
        confidence=confidence,
    )
