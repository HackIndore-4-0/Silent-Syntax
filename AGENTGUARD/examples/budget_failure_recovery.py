"""The canonical Phase 3 demo — a real, runnable proof of:

    execution -> evaluation -> failure detection -> root-cause
    identification -> checkpoint recovery -> rollback ->
    counterfactual safe-path analysis -> re-execution/recovery ->
    successful completion -> auditable history

exactly the scenario the Final Solution spec's canonical budget-failure
example describes: a budget constraint (max_cost=60000) silently
disappears from an agent's own state at S3, so its S4 selection
(67000) is never checked against it — the deterministic
ConstraintAdherenceEvaluator only sees the final state and would (on
its own) report the violation at S4/S5, one step later than the real
break. Phase 2's Root-Cause Engine already correctly attributes it to
S3 (see docs/EXECUTION_REPORT_PHASE_2.md §7); Phase 3 adds the ability
to actually recover from it.

Environment note: this repository has no reachable PostgreSQL in this
development environment (no `docker`, no `AGENTGUARD_DATABASE_URL` — see
docs/EXECUTION_REPORT_PHASE_3.md §1, the same limitation Phase 1/2
already documented). This script therefore configures AgentGuard with
`agentguard.storage.memory.InMemoryRunRepository`, the exact same
`RunRepository` implementation the test suite runs against — every
mechanism exercised here (checkpoints, hash-chained audit trail,
rollback, counterfactual analysis) runs through the identical interface
`PostgresRunRepository` implements; swapping in a real database is a
one-line `agentguard.configure(...)` change (see README.md).

Run with:

    .venv/Scripts/python.exe examples/budget_failure_recovery.py    (Windows)
    python examples/budget_failure_recovery.py                       (Postgres configured)
"""
from __future__ import annotations

import asyncio
import json

import agentguard
from agentguard import Policy, monitor, reset_state, update_state
from agentguard._runtime import configure, reset
from agentguard.audit.chain import verify_audit_chain
from agentguard.recovery import generate_counterfactual, rollback, seed_recovery_state
from agentguard.reliability.report import build_reliability_report
from agentguard.storage.memory import InMemoryRunRepository

POLICY = Policy(max_cost=60000, version=3)


def _p(label: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")


def _kv(k: str, v) -> None:
    print(f"  {k}: {v}")


@monitor(policy=POLICY, llm_judge=False)
async def drifting_agent(task: str) -> str:
    """S1/S2: max_budget=60000. S3: the constraint is silently lost
    (reset_state()). S4: the agent, with nothing left to check it
    against, selects a 67000 item."""
    update_state(max_budget=60000)  # S2
    reset_state()  # S3 — the constraint disappears here (the real root cause)
    update_state(max_budget=67000)  # S4
    return "selected a 67000 laptop"


@monitor(policy=POLICY, llm_judge=False)
async def safe_recovery_agent(task: str) -> str:
    """The recovery attempt: starts from whatever state
    agentguard.recovery.seed_recovery_state() restored (max_budget still
    60000, from checkpoint S2) and, this time, keeps the constraint
    intact while selecting a compliant item."""
    update_state(max_budget=52000)
    return "selected a 52000 laptop"


async def main() -> None:
    reset()
    repository = InMemoryRunRepository()
    configure(repository)

    _p("STEP 1 — EXECUTION")
    result = await drifting_agent("find a laptop under budget")
    original_run = await repository.get_run(_only_run_id(repository, exclude=set()))
    run_id = original_run["id"]
    _kv("run_id", run_id)
    _kv("policy_version", original_run["policy"]["version"])
    _kv("agent result", result)

    checkpoints = await repository.list_checkpoints(run_id)
    _p("STEP 2 — STATE SEQUENCE (checkpoints S1..S4)")
    for c in checkpoints:
        _kv(c["label"], c["state"])

    _p("STEP 3 — EVALUATION / FAILURE DETECTED")
    constraint_eval = next(e for e in original_run["evaluations"] if e["evaluator"] == "constraint_adherence")
    _kv("constraint_adherence.passed", constraint_eval["passed"])
    _kv("violation", f"{constraint_eval['evidence']['observed']} > {constraint_eval['evidence']['expected']}")
    _kv("run.status", original_run["status"])
    assert original_run["status"] == "stop", "canonical demo requires the original run to STOP"
    assert constraint_eval["evidence"]["observed"] == 67000
    assert constraint_eval["evidence"]["expected"] == 60000.0

    _p("STEP 4 — ROOT CAUSE IDENTIFICATION")
    root_cause = await repository.get_root_cause(run_id)
    _kv("earliest_deviation", root_cause["earliest_deviation"])
    _kv("explanation", root_cause["explanation"])
    _kv("confidence", root_cause["confidence"])
    assert root_cause["earliest_deviation"] == "S3", "root cause must precede the S4/S5 evaluator-visible failure"

    _p("STEP 5 — STOP (decision already recorded during execution)")
    decisions = original_run["decisions"]
    stop_decision = decisions[-1]
    _kv("decision.outcome", stop_decision["outcome"])
    _kv("decision.reason", stop_decision["reason"])
    assert stop_decision["outcome"] == "stop"

    _p("STEP 6 — CHECKPOINT ROLLBACK")
    safe_checkpoint_label = "S2"  # the checkpoint immediately before S3, the root cause
    rollback_result = await rollback(repository, run_id, safe_checkpoint_label)
    _kv("rolled back to", f"{rollback_result.checkpoint.label} ({rollback_result.checkpoint.id})")
    _kv("previous_state", rollback_result.previous_state)
    _kv("restored_state", rollback_result.restored_state)
    _kv("decision.outcome", rollback_result.decision.outcome.value)
    assert rollback_result.restored_state.get("max_budget") == 60000

    _p("STEP 7 — RE-EXECUTION / RECOVERY (from the restored, constraint-intact state)")
    seed_recovery_state(
        rollback_result.restored_state, parent_run_id=run_id, checkpoint_id=rollback_result.checkpoint.id
    )
    recovered_result = await safe_recovery_agent("retry the task with the budget constraint intact")
    recovery_run_id = _only_run_id(repository, exclude={run_id})
    recovery_run = await repository.get_run(recovery_run_id)
    _kv("recovery run_id", recovery_run_id)
    _kv("recovery run.parent_run_id", recovery_run["parent_run_id"])
    _kv("recovery run.status", recovery_run["status"])
    _kv("recovery agent result", recovered_result)
    assert recovery_run["status"] == "continue", "the recovered run must succeed"
    assert recovery_run["final_state"]["max_budget"] == 52000

    _p("STEP 8 — COUNTERFACTUAL ANALYSIS (actual vs. counterfactual)")
    recovery_checkpoints = await repository.list_checkpoints(recovery_run_id)
    post_rollback_run = await repository.get_run(run_id)
    cf = generate_counterfactual(
        post_rollback_run, checkpoints, recovery_run, recovery_checkpoints, rollback_result.checkpoint.id, root_cause
    )
    await repository.save_counterfactual(cf)
    print("  ACTUAL PATH:")
    for step in cf.actual_path:
        print(f"    {step['label']}: {step['data']}" + (f"  [{step['status']}]" if "status" in step else ""))
    print("  COUNTERFACTUAL PATH:")
    for step in cf.counterfactual_path:
        print(f"    {step['label']}: {step['data']}" + (f"  [{step['status']}]" if "status" in step else ""))
    _kv("result", cf.result)
    _kv("confidence", cf.confidence)
    print(
        "  NOTE: the counterfactual path above is reconstructed from a real, separately\n"
        "  executed recovery run — NOT a literal replay of the original run's history."
    )

    _p("STEP 9 — AUDIT INTEGRITY")
    verification = await verify_audit_chain(repository, run_id)
    _kv("event_count", verification["event_count"])
    _kv("intact", verification["intact"])
    assert verification["intact"] is True

    _p("STEP 10 — TAMPER DETECTION (demonstration on an isolated copy of the chain)")
    tampered_events = repository.audit_events[run_id]
    original_payload = dict(tampered_events[2].payload)
    tampered_events[2].payload["tampered_field"] = "unauthorized edit"
    tampered_verification = await verify_audit_chain(repository, run_id)
    _kv("intact (after tamper)", tampered_verification["intact"])
    _kv("first_invalid_event", tampered_verification["first_invalid_event"])
    assert tampered_verification["intact"] is False
    assert tampered_verification["first_invalid_event"] == tampered_events[2].id
    tampered_events[2].payload = original_payload
    restored_verification = await verify_audit_chain(repository, run_id)
    _kv("intact (after restore)", restored_verification["intact"])
    assert restored_verification["intact"] is True

    _p("STEP 11 — SIX-DIMENSION RELIABILITY REPORT (recovery run)")
    report = build_reliability_report(
        recovery_run,
        risk_assessments=await repository.list_risk_assessments(recovery_run_id),
        root_cause=await repository.get_root_cause(recovery_run_id),
        audit_events=await repository.list_audit_events(recovery_run_id),
    )
    print(json.dumps(report.to_dict(), indent=2, default=str))

    _p("SUMMARY")
    print(
        f"  DETECTED -> ROOT CAUSE ({root_cause['earliest_deviation']}) -> STOP -> "
        f"ROLLBACK ({rollback_result.checkpoint.label}) -> COUNTERFACTUAL -> "
        f"RECOVERY ({recovery_run['status'].upper()}) -> SUCCESS"
    )
    print(f"  original_run_id:  {run_id}")
    print(f"  recovery_run_id:  {recovery_run_id}")
    print("  audit chain:      INTACT")


def _only_run_id(repository: InMemoryRunRepository, *, exclude: set[str]) -> str:
    candidates = [rid for rid in repository.runs if rid not in exclude]
    assert len(candidates) == 1, f"expected exactly one new run, found {candidates}"
    return candidates[0]


if __name__ == "__main__":
    asyncio.run(main())
