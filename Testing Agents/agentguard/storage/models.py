"""Row-shaped DTOs describing AgentGuard's PostgreSQL schema.

Kept separate from agentguard.models (the SDK-facing domain models) so
the persistence shape can evolve — new columns, a spans table with real
parent/child rows once Phase 2 adds tool/LLM spans — without changing
what @monitor and the evaluators work with. Phase 1's postgres.py maps
between the two by hand; nothing here is imported outside storage/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RunRow:
    id: str
    agent_name: str
    task: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    initial_state: dict[str, Any]
    final_state: dict[str, Any] | None
    exception_type: str | None
    exception_message: str | None
    trace_id: str | None
    span_id: str | None


@dataclass
class SpanRow:
    run_id: str
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_time: datetime
    end_time: datetime | None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyRow:
    run_id: str
    max_cost: float | None


@dataclass
class EvaluationRow:
    run_id: str
    evaluator: str
    passed: bool
    score: float
    label: str
    evidence: dict[str, Any]


@dataclass
class DecisionRow:
    run_id: str
    outcome: str
    reason: str
    evidence: dict[str, Any]
    risk_score: float | None = None
    confidence: float | None = None
    retry_count: int = 0
    replan_count: int = 0


@dataclass
class RiskAssessmentRow:
    run_id: str
    action: str | None
    risk_score: float
    impact: float
    confidence: float
    factors: dict[str, Any]
    weights: dict[str, Any]
    explanation: str


@dataclass
class RootCauseRow:
    run_id: str
    earliest_deviation: str
    expected: dict[str, Any]
    observed: dict[str, Any]
    confidence: float
    explanation: str
    evidence: dict[str, Any]


@dataclass
class CheckpointRow:
    id: str
    run_id: str
    label: str
    seq: int
    state_hash: str
    state: dict[str, Any]
    valid: bool
    created_at: datetime


@dataclass
class AuditEventRow:
    id: str
    run_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    policy_version: int | None
    created_at: datetime


@dataclass
class CounterfactualRow:
    id: str
    run_id: str
    source_checkpoint_id: str
    actual_path: list[Any]
    counterfactual_path: list[Any]
    altered_state: dict[str, Any]
    result: str
    comparison: dict[str, Any]
    confidence: float
    created_at: datetime


@dataclass
class HumanDecisionRow:
    id: str
    run_id: str
    action: str | None
    decision: str
    risk_score: float | None
    confidence: float | None
    reason: str
    evidence: dict[str, Any]
    status: str
    timeout_s: float
    requested_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
