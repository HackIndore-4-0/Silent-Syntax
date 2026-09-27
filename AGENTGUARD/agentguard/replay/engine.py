"""Failure Replay — Phase 4.

`replay(repository, run_id)` reconstructs the context available to the
agent at each recorded decision point into an ordered, inspectable
sequence of `ReplayStep`s — never just a raw dump of stored logs. It
interleaves the run's checkpoints (real state, joined in from
`agentguard_checkpoints`) with its hash-chained audit events (already
themselves ordered and typed — RUN_START, AGENT_STEP, POLICY_CHECK,
EVALUATION, TOOL_CALL, TOOL_SUBSTITUTED, RISK_ASSESSMENT, ROOT_CAUSE,
DECISION, HUMAN, ROLLBACK, RUN_COMPLETION) into one timeline, classifying
each step's `kind` so a caller can filter/inspect ("show me only the
decision points", "show me only state") without re-deriving that
classification itself.

SAFETY (Rule: "Do not execute external side effects during a historical
replay"): this module never calls agent code, never calls a registered
tool callable, and never re-runs anything. It only reads already-
persisted data. `ReplaySession.mode` is always `"safe"` — there is no
other mode in Phase 4 (a live/authorized replay mode is explicitly
deferred, per the spec, to a later phase).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StepKind = Literal["event", "state", "evaluation", "decision"]

_KIND_BY_EVENT_TYPE: dict[str, StepKind] = {
    "RUN_START": "event",
    "AGENT_STEP": "event",
    "POLICY_CHECK": "event",
    "EVALUATION": "evaluation",
    "TOOL_CALL": "event",
    "TOOL_SUBSTITUTED": "event",
    "RISK_ASSESSMENT": "event",
    "ROOT_CAUSE": "event",
    "DECISION": "decision",
    "CHECKPOINT": "state",
    "HUMAN": "event",
    "ROLLBACK": "decision",
    "RUN_COMPLETION": "event",
}


@dataclass
class ReplayStep:
    step: int
    label: str
    kind: StepKind
    data: dict[str, Any]
    timestamp: Any = None


@dataclass
class ReplaySession:
    run_id: str
    mode: Literal["safe"] = "safe"
    no_external_side_effects: bool = True
    task: str | None = None
    input: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    policy_version: int | None = None
    agent_version: str | None = None
    model_metadata: dict[str, Any] | None = None
    root_cause: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    confidence: float | None = None
    steps: list[ReplayStep] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["REPLAY MODE", "NO EXTERNAL SIDE EFFECTS", f"run_id: {self.run_id}"]
        for s in self.steps:
            lines.append(f"Step {s.step}\n{s.label}")
        return "\n".join(lines)


def _extract_model_metadata(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    judge = next((e for e in evaluations if e.get("evaluator") == "llm_judge"), None)
    if judge is None:
        return {}
    evidence = judge.get("evidence") or {}
    return {
        "provider": evidence.get("provider"),
        "provider_is_real_llm": evidence.get("provider_is_real_llm"),
        "correctness": evidence.get("correctness"),
        "goal_completion": evidence.get("goal_completion"),
    }


async def replay(repository: Any, run_id: str) -> ReplaySession:
    run = await repository.get_run(run_id)
    if run is None:
        raise ValueError(f"run {run_id!r} not found")

    checkpoints = await repository.list_checkpoints(run_id)
    checkpoints_by_id = {c["id"]: c for c in checkpoints}
    audit_events = await repository.list_audit_events(run_id)
    root_cause = await repository.get_root_cause(run_id)
    risk_assessments = await repository.list_risk_assessments(run_id)
    latest_risk = risk_assessments[-1] if risk_assessments else None

    session = ReplaySession(
        run_id=run_id,
        task=run.get("task"),
        input={"initial_state": run.get("initial_state")},
        policy=run.get("policy"),
        policy_version=(run.get("policy") or {}).get("version"),
        agent_version=run.get("agent_version"),
        model_metadata=_extract_model_metadata(run.get("evaluations") or []),
        root_cause=root_cause,
        risk={"risk_score": latest_risk.get("risk_score"), "impact": latest_risk.get("impact")} if latest_risk else None,
        confidence=latest_risk.get("confidence") if latest_risk else None,
    )

    step_no = 0
    for event in audit_events:
        step_no += 1
        kind = _KIND_BY_EVENT_TYPE.get(event["event_type"], "event")
        data = dict(event["payload"])

        if event["event_type"] == "CHECKPOINT":
            checkpoint = checkpoints_by_id.get(data.get("checkpoint_id"))
            if checkpoint is not None:
                data = {**data, "state": checkpoint["state"]}
            label = f"State: {data.get('state', {})}"
        elif event["event_type"] == "DECISION":
            label = f"Decision: {data.get('outcome', '').upper()} — {data.get('reason', '')}"
        elif event["event_type"] == "EVALUATION":
            label = f"Evaluation: {data.get('evaluator')} = {data.get('label')} (passed={data.get('passed')})"
        elif event["event_type"] == "ROOT_CAUSE":
            label = f"Root cause: {data.get('earliest_deviation')} — {data.get('explanation')}"
        elif event["event_type"] == "TOOL_CALL":
            label = f"Tool call: {data.get('tool')} -> {data.get('outcome')}"
        elif event["event_type"] == "TOOL_SUBSTITUTED":
            label = f"Tool substituted: {data.get('primary')} -> {data.get('fallback')}"
        elif event["event_type"] == "RUN_START":
            label = "Task received"
        elif event["event_type"] == "RUN_COMPLETION":
            label = f"Run completed: {data.get('status', '').upper()}"
        else:
            label = event["event_type"].replace("_", " ").title()

        session.steps.append(
            ReplayStep(step=step_no, label=label, kind=kind, data=data, timestamp=event.get("created_at"))
        )

    return session
