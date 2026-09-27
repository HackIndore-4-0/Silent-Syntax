"""Domain models shared across the AgentGuard SDK.

These are the objects @monitor, the evaluators, and the Decision Engine
operate on. They are intentionally separate from agentguard.storage.models,
which describes how the same information is shaped once persisted.

Phase 2 extends this module with the RETRY/REPLAN/HUMAN vocabulary, a
richer declarative Policy, and the structured evidence objects the
Reliability Engine produces (PolicyFinding, RiskAssessment, RootCause,
HumanDecision). Nothing below is a stub: every field here is actually
populated by Phase 2 code (see decision/engine.py, reliability/, policy/).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    return str(uuid.uuid4())


class RunStatus(str, Enum):
    """The Decision Engine's states (see decision/engine.py).

    Phase 1 flow: RUNNING -> EVALUATING -> CONTINUE | STOP | FAILED.

    Phase 2 extends this with RETRY, REPLAN, and HUMAN — all real,
    reachable states now (see docs/PHASE2.md and decision/engine.py for
    the full transition table).
    """

    RUNNING = "running"
    EVALUATING = "evaluating"
    CONTINUE = "continue"
    RETRY = "retry"
    REPLAN = "replan"
    HUMAN = "human"
    STOP = "stop"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    """Phase 3 addition: a completed run's state was restored to an
    earlier checkpoint via agentguard.recovery.rollback(). Reachable only
    from a terminal-ish state (STOP/FAILED/HUMAN/CONTINUE), never from
    mid-execution (RUNNING/EVALUATING/RETRY/REPLAN) — see
    decision/engine.py's rollback()."""


Severity = Literal["low", "medium", "high", "critical"]
UncertainFallback = Literal["continue", "stop", "human"]
HumanOutcome = Literal["pending", "approved", "rejected", "replan", "timeout"]


class ModelAlternative(BaseModel):
    """One primary -> fallback model mapping for the LLM Gateway
    (agentguard/tracing/litellm_wrap.py), explicitly registered —
    mirrors ToolAlternative's "no arbitrary substitution" rule exactly.
    Both `primary_model`/`fallback_model` are litellm model strings
    (e.g. "gpt-4o-mini", "anthropic/claude-3-5-sonnet-latest") — litellm
    itself resolves the provider, so unlike ToolAlternative there is no
    separate "which client" registration needed."""

    primary_model: str
    fallback_model: str
    reliability_threshold: float = 0.8
    workspace_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


class LLMGatewayPolicy(BaseModel):
    """Optional per-run governance for LLM calls made through
    agentguard.tracing.litellm_wrap's traced_completion()/
    traced_acompletion() (or wrap_llm_client(), for the allow-list/
    token-ceiling checks only — fallback routing is litellm-specific).
    None on Policy (the default) means "no gateway governance" — fully
    backward compatible with every existing caller."""

    allowed_models: list[str] = Field(default_factory=list)
    """Empty = no allow-list restriction (any model permitted).
    Non-empty = only these model names may be called; anything else
    raises LLMGatewayViolation immediately — checked from the call's
    `model=` argument before the real call is made, no I/O involved,
    same synchronous guarantee as Policy.forbidden_actions."""

    max_tokens_per_call: int | None = None
    """Ceiling on the requested max_tokens in the call kwargs, checked
    BEFORE the real call (a request-side ceiling — actual tokens used
    are only known after the call returns, too late to block)."""

    max_cost_per_call_usd: float | None = None
    """Checked AFTER the call, against the real cost litellm computed
    (litellm.completion_cost()) — cost can only be known once the
    provider has actually responded, so a violation here can only ever
    be reported, never used to block the call that already happened."""

    on_violation: Literal["raise", "human"] = "raise"
    """"raise": LLMGatewayViolation immediately (deterministic, known
    before the call — same precedent as forbidden_actions). "human":
    routed through the existing perform_action()/request_approval()
    broker (same precedent as require_approval) — async call path only,
    since blocking a sync call on a human decision needs an event loop
    that isn't available there."""

    fallback_chain: list[ModelAlternative] = Field(default_factory=list)


class CircuitBreakerPolicy(BaseModel):
    """Optional per-run execution circuit breaker, enforced inside
    agentguard.call_tool() (agentguard/context.py). None on Policy (the
    default) means "no breaker" — fully backward compatible with every
    existing call_tool() caller.

    Distinct from retry_limit/max_replans: those bound how many times a
    SINGLE call_tool() invocation retries/replans after its OWN primary
    tool + fallback are exhausted. This is a run-level ceiling that
    looks across the whole run — many different tools each "succeeding"
    at their own retry budget can still add up to a run that is
    obviously stuck, which neither of those fields would ever catch."""

    max_consecutive_tool_failures: int | None = None
    """Cross-tool counter of consecutive failure/timeout ToolCallEvents
    — incremented for ANY tool's failure, reset to 0 by ANY tool's
    success. None = unbounded (today's behavior). Checked, and raises
    CircuitBreakerTripped immediately, BEFORE call_tool()'s existing
    fallback/replan logic runs for that failure — a hard stop takes
    priority over the softer per-tool recovery path."""

    max_loop_iterations: int | None = None
    """Ceiling on the number of agentguard.call_tool() invocations in
    one run — the natural per-iteration marker for a reason-act-observe
    agent loop. Checked at the top of call_tool(), before the tool is
    even attempted, so a runaway loop never makes call N. None =
    unbounded (today's behavior)."""


class Policy(BaseModel):
    """Declarative safety boundary attached to a monitored run.

    Phase 1 implemented `max_cost` only. Phase 2 adds the rest of the
    fields the Final Solution spec calls for: forbidden/approval-gated
    actions, an explicit fallback for uncertain evaluations, and the
    bounds that keep RETRY/REPLAN from looping forever.

    Example:
        Policy(
            max_cost=60000,
            require_approval=["payment"],
            forbidden_actions=["drop_database"],
            default_on_uncertain="human",
        )
    """

    max_cost: float | None = None

    # Phase 2 additions -----------------------------------------------
    require_approval: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    default_on_uncertain: UncertainFallback = "stop"

    retry_limit: int = 2
    """Maximum number of RETRY transitions before falling through to
    `on_retry_exhausted`."""

    on_retry_exhausted: Literal["replan", "stop"] = "replan"

    max_replans: int = 1
    """Maximum number of REPLAN transitions before the run is stopped."""

    human_timeout_s: float = 120.0
    """Deterministic timeout for a HUMAN approval request. On timeout the
    request is denied (see human/broker.py) — the agent never hangs
    forever waiting for a person."""

    on_tool_exhausted: Literal["replan", "human"] = "replan"
    """Phase 4: what happens when agentguard.call_tool()'s primary tool
    fails and no reliable registered alternative is available (or the
    alternative also fails). "replan" raises ReplanRequested (bounded by
    max_replans, same as any other REPLAN); "human" escalates via the
    existing perform_action/request_approval HUMAN channel instead."""

    version: int = 1
    """Phase 3 addition: the declarative version of this Policy. Every
    audit-relevant event recorded for a run references the
    `policy.version` active for that run at the time it started
    (AuditEvent.policy_version) — a completed run's policy version is
    never changed retroactively. Bump this by hand whenever you change a
    Policy's meaning (e.g. tightening max_cost); AgentGuard does not
    infer version changes automatically."""

    llm_gateway: LLMGatewayPolicy | None = None
    """Optional per-run LLM call governance (agentguard/tracing/litellm_wrap.py).
    None (the default) preserves every existing wrap_llm_client()/
    traced_completion() caller's exact current behavior — pure
    observation, no enforcement."""

    circuit_breaker: CircuitBreakerPolicy | None = None
    """Optional per-run execution circuit breaker (agentguard/context.py's
    call_tool()). None (the default) preserves every existing call_tool()
    caller's exact current behavior — no consecutive-failure or
    loop-iteration ceiling enforced."""


class PolicyFinding(BaseModel):
    """Structured output of a single declarative policy rule check.

    Example:
        PolicyFinding(
            rule="max_cost", violated=True,
            expected=60000, observed=67000, severity="high",
        )
    """

    rule: str
    violated: bool
    severity: Severity = "low"
    expected: Any = None
    observed: Any = None
    detail: str = ""


class AgentState(BaseModel):
    """Typed, extensible key/value state captured for a run.

    Any keyword becomes a field, e.g. AgentState(max_budget=60000). A
    snapshot is a plain dict copy. Phase 2's Root-Cause Engine diffs an
    ordered sequence of these snapshots (see StateSnapshot below and
    context.py, which records one on every update_state() call).
    """

    model_config = ConfigDict(extra="allow")

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_snapshot(cls, data: dict[str, Any] | None) -> "AgentState":
        return cls(**(data or {}))


class StateSnapshot(BaseModel):
    """One ordered point in a run's state history.

    `label` follows the canonical S1, S2, S3... convention used
    throughout the Phase 2 spec and in RootCause.earliest_deviation.
    """

    label: str
    seq: int
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class EvalResult(BaseModel):
    """Structured output of a single evaluator run.

    Phase 2 adds `confidence` (how sure the evaluator is of its own
    score/label) and `reason` (a human-readable justification), on top
    of Phase 1's evaluator/passed/score/label/evidence.
    """

    evaluator: str
    passed: bool
    score: float
    label: str
    confidence: float = 1.0
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    """Dynamic per-action/per-evaluation risk score.

    See reliability/risk.py for the exact (documented, deterministic)
    formula. `factors` holds the raw 0..1 factor values that were
    combined; `weights` holds the coefficients used, so the number is
    never a black box.
    """

    run_id: str
    action: str | None = None
    risk_score: float
    impact: float
    confidence: float
    factors: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    explanation: str = ""
    created_at: datetime = Field(default_factory=_now)


class RootCause(BaseModel):
    """Output of the causal state-diff Root-Cause Engine.

    Example:
        RootCause(
            earliest_deviation="S3",
            expected={"max_budget": 60000},
            observed={"max_budget": None},
            confidence=0.97,
            explanation="max_budget disappeared during state transition S3",
        )
    """

    run_id: str
    earliest_deviation: str
    expected: dict[str, Any] = Field(default_factory=dict)
    observed: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class HumanDecision(BaseModel):
    """A human-in-the-loop approval request and its (eventual) outcome."""

    id: str = Field(default_factory=new_run_id)
    run_id: str
    action: str | None = None
    decision: RunStatus
    """The Decision Engine outcome that triggered this request (HUMAN)."""
    risk_score: float | None = None
    confidence: float | None = None
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: HumanOutcome = "pending"
    timeout_s: float = 120.0
    requested_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class Decision(BaseModel):
    """A recorded Decision Engine outcome.

    Phase 2 adds risk_score/confidence/policy_findings/root_cause so a
    decision is never just "AI thinks this is unsafe" — every STOP/
    HUMAN/RETRY/REPLAN carries the structured evidence that produced it.
    """

    outcome: RunStatus
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)

    risk_score: float | None = None
    confidence: float | None = None
    policy_findings: list[PolicyFinding] = Field(default_factory=list)
    retry_count: int = 0
    replan_count: int = 0


class Run(BaseModel):
    """A single @monitor-wrapped execution."""

    id: str = Field(default_factory=new_run_id)
    agent_name: str
    task: str | None = None
    policy: Policy = Field(default_factory=Policy)
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)
    final_state: dict[str, Any] | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    retry_count: int = 0
    replan_count: int = 0

    # Phase 3 additions --------------------------------------------------
    actions: list[dict[str, Any]] = Field(default_factory=list)
    """Every agentguard.perform_action() call recorded during this run —
    persisted so the Reliability Report's Tool Usage dimension (and any
    later audit question about "what did the agent actually do") can be
    answered post-hoc, not just while the run is live in memory."""

    parent_run_id: str | None = None
    """Set only on a recovery run created via
    agentguard.recovery.seed_recovery_state() — the run_id this run is
    recovering from. None for an ordinary, non-recovery run."""

    recovery_checkpoint_id: str | None = None
    """The Checkpoint this run's initial state was restored from, if this
    run is a recovery run (parent_run_id is set)."""

    # Phase 4 addition ----------------------------------------------------
    agent_version: str | None = None
    """Optional caller-supplied label (`@monitor(agent_version="v2")`)
    identifying which version of the agent's own code/prompt produced
    this run — what Regression Comparison ("Agent v1" vs "Agent v2")
    groups runs by. None means the caller never labeled it; comparison
    still works run-to-run without it."""

    # Dashboard V2 addition (multi-tenancy) --------------------------------
    workspace_id: str | None = None
    """Which authenticated workspace this run belongs to — the
    isolation boundary User A vs. User B is enforced on. None for a
    plain `agentguard.monitor`/`@monitor` call made without an
    `AgentGuard` client (single-tenant/local/CLI/test usage — unchanged,
    backward-compatible behavior)."""

    project_id: str | None = None
    """Which project within `workspace_id` this run belongs to (e.g.
    "production", "development"). None when workspace_id is None."""

    agent_id: str | None = None
    """The AgentRegistration this run's agent_name resolved to within
    workspace_id, auto-created on first use. None when workspace_id is
    None."""

    model_name: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    estimated_cost_usd: float | None = None
    """Token/cost metadata (Dashboard V2's Tokens & Cost page) — set via
    `agentguard.record_tokens(...)` by agent code that made a real LLM
    call and knows its usage. None (never 0 or a guess) whenever the
    agent never reported it — the dashboard shows "N/A" for these runs
    rather than fabricating a number (Rule: "If token/cost metadata is
    unavailable, display N/A rather than fake data")."""

    @property
    def duration_ms(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds() * 1000


class Checkpoint(BaseModel):
    """A typed, JSON-safe AgentState snapshot suitable for rollback.

    One Checkpoint is created per StateSnapshot (S1, S2, S3, ... — the
    same ordered sequence agentguard.update_state()/reset_state() build,
    see context.py) once a run finishes (decorator.py). `state` is
    exactly `StateSnapshot.data` (already a plain dict via
    AgentState.snapshot()/model_dump()) — nothing here ever serializes an
    arbitrary Python runtime object. `state_hash` is the SHA-256 of the
    checkpoint's own canonical state JSON, so a stored checkpoint's
    integrity can be verified independently of the run's audit hash
    chain (see agentguard/checkpoint/engine.py).
    """

    id: str = Field(default_factory=new_run_id)
    run_id: str
    label: str
    seq: int
    state_hash: str
    state: dict[str, Any] = Field(default_factory=dict)
    valid: bool = True
    created_at: datetime = Field(default_factory=_now)


class CounterfactualResult(BaseModel):
    """Reconstructed 'what would have happened if the corrupted state had
    not occurred' analysis (see agentguard/recovery/counterfactual.py).

    This is NOT a claim about a literal historical event: `counterfactual_path`
    is built from a real, separately-executed recovery run (its own Run
    row, its own audit trail, its own wall-clock time) that started from
    `source_checkpoint_id`'s retained state — never an invented number.
    """

    id: str = Field(default_factory=new_run_id)
    run_id: str
    source_checkpoint_id: str
    actual_path: list[dict[str, Any]] = Field(default_factory=list)
    counterfactual_path: list[dict[str, Any]] = Field(default_factory=list)
    altered_state: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    comparison: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    created_at: datetime = Field(default_factory=_now)


class AuditEvent(BaseModel):
    """One SHA-256 hash-chained entry in a run's tamper-evident audit
    trail.

        event_hash = SHA256(canonical_json(payload) + previous_hash)

    See agentguard/audit/hashchain.py for the exact canonicalization and
    agentguard/audit/chain.py for chain construction/verification.
    """

    id: str = Field(default_factory=new_run_id)
    run_id: str
    seq: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str
    event_hash: str
    policy_version: int | None = None
    created_at: datetime = Field(default_factory=_now)


# =====================================================================
# Phase 4 — replay, regression comparison, tool reliability, alternative-
# tool recovery, behavior fingerprinting, auto-improvement, policy
# control portal, CI/CD gate.
# =====================================================================

ToolOutcome = Literal["success", "failure", "timeout"]


class ToolCallEvent(BaseModel):
    """One recorded agentguard.call_tool() invocation.

    Raw, per-call evidence — ToolProfile (reliability/tool_profile.py) is
    the aggregated statistic computed FROM a tool's accumulated
    ToolCallEvent history, never stored redundantly itself.
    """

    id: str = Field(default_factory=new_run_id)
    run_id: str
    tool: str
    attempt: int = 1
    outcome: ToolOutcome
    duplicate: bool = False
    is_fallback: bool = False
    """True if this call was to a registered alternative, not the
    primary tool named by the agent."""
    latency_ms: float = 0.0
    error: str = ""
    created_at: datetime = Field(default_factory=_now)


TraceStepKind = Literal["function", "llm_call"]
TraceStepOutcome = Literal["success", "failure"]


class TraceStep(BaseModel):
    """One recorded @traceable-wrapped call, or an equivalent
    wrap_llm_client-wrapped LLM call, made during an already-@monitor-
    wrapped run (see agentguard/tracing/).

    Flat shape, mirroring ToolCallEvent — `parent_step_id` is the only
    nesting field, set automatically from the ambient "current step"
    contextvar (agentguard/tracing/context.py), never supplied by the
    caller. Modeled after agentguard_spans.parent_span_id, the only
    existing nesting precedent in the schema: nullable, no
    self-referential FK constraint.
    """

    id: str = Field(default_factory=new_run_id)
    run_id: str
    parent_step_id: str | None = None
    kind: TraceStepKind
    name: str
    """The traced function's __qualname__, or the wrapped LLM call's
    dotted method name (e.g. "chat.completions.create")."""
    input: dict[str, Any] = Field(default_factory=dict)
    """{"args": [...], "kwargs": {...}} — JSON-safe on the Postgres
    write path via json.dumps(default=str), same convention as every
    other JSONB column in this codebase."""
    output: Any = None
    outcome: TraceStepOutcome
    latency_ms: float = 0.0
    exception_type: str | None = None
    exception_message: str | None = None
    traceback_text: str | None = None
    code_file: str | None = None
    code_function: str | None = None
    code_lineno: int | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    """kind="llm_call" only, and only when extraction actually
    produced a number — never fabricated, matching record_tokens()'s
    own rule."""
    model_name: str | None = None
    """kind="llm_call" only. Populated when derivable from the
    response (litellm's ModelResponse.model, or duck-typed from a raw
    SDK response/call kwargs) — never guessed, same rule as
    tokens_input/cost_usd."""
    created_at: datetime = Field(default_factory=_now)


class ToolProfile(BaseModel):
    """A tool's aggregated reliability statistics, computed fresh from
    its ToolCallEvent history each time (never a separately-stored,
    independently-staleable copy) — see
    agentguard/reliability/tool_profile.py for the exact thresholds.
    """

    tool: str
    sample_count: int
    success_rate: float | None = None
    failure_rate: float | None = None
    timeout_rate: float | None = None
    retry_rate: float | None = None
    duplicate_call_rate: float | None = None
    latency_mean_ms: float | None = None
    latency_p95_ms: float | None = None
    """None when fewer than MIN_P95_SAMPLES samples exist — a p95 over
    too few points is not meaningful (see the engine's documented
    threshold)."""
    reliability: Literal["RELIABLE", "DEGRADED", "UNRELIABLE", "INSUFFICIENT_DATA"]
    min_sample_size: int


class ModelProfile(BaseModel):
    """A model's aggregated reliability statistics, computed fresh from
    its TraceStep(kind="llm_call") history each time — mirrors
    ToolProfile exactly, see agentguard/reliability/model_profile.py
    for the exact thresholds. timeout_rate/retry_rate/duplicate_call_rate
    are always None (not 0.0): TraceStep has no such fields, so "not
    tracked" stays distinct from "measured as zero"."""

    model: str
    sample_count: int
    success_rate: float | None = None
    failure_rate: float | None = None
    timeout_rate: float | None = None
    retry_rate: float | None = None
    duplicate_call_rate: float | None = None
    latency_mean_ms: float | None = None
    latency_p95_ms: float | None = None
    avg_tokens_input: float | None = None
    avg_tokens_output: float | None = None
    avg_cost_usd: float | None = None
    reliability: Literal["RELIABLE", "DEGRADED", "UNRELIABLE", "INSUFFICIENT_DATA"]
    min_sample_size: int


class ToolAlternative(BaseModel):
    """An explicitly registered primary -> fallback tool mapping.
    agentguard.call_tool() never substitutes a tool that isn't
    registered here (Rule: "Do not allow arbitrary tool substitution")."""

    primary: str
    fallback: str
    reliability_threshold: float = 0.8
    created_at: datetime = Field(default_factory=_now)
    workspace_id: str | None = None
    """Isolation boundary (Dashboard V2). None = the pre-Dashboard-V2,
    single-tenant/global registration behavior, unchanged."""


class BehaviorFingerprint(BaseModel):
    """An agent's historical behavior baseline vs. one current run,
    computed fresh each time from persisted Run/ToolCallEvent/Decision
    history — see agentguard/reliability/fingerprint.py.
    """

    agent_name: str
    sample_count: int
    """Number of PRIOR runs (excluding the current one being checked)
    the baseline was computed from."""
    baseline: dict[str, float | None] | None = None
    current: dict[str, float | None] | None = None
    deviation: dict[str, float | None] | None = None
    """Relative deviation per metric: (current - baseline) / baseline.
    A metric's own deviation is None when it cannot be meaningfully
    computed (e.g. both baseline and current are exactly zero)."""
    anomaly: bool = False
    min_sample_size: int = 5
    anomaly_threshold: float = 0.5
    """A metric is flagged only once sample_count >= min_sample_size
    AND its relative deviation exceeds this fraction (default 50%)."""


class PolicyDefinition(BaseModel):
    """A named, versioned Policy managed through the Control Portal —
    distinct from the per-run Policy snapshot every Run already carries
    (that snapshot is immutable and untouched by anything here). Saving
    a new version here never mutates a prior version or any run that
    already used it."""

    name: str
    current_version: int
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    workspace_id: str | None = None
    """Isolation boundary (Dashboard V2). Policy names are unique per
    workspace, not globally, once this is set."""


class PolicyVersionRecord(BaseModel):
    id: str = Field(default_factory=new_run_id)
    policy_name: str
    workspace_id: str | None = None
    version: int
    policy: Policy
    created_at: datetime = Field(default_factory=_now)


ImprovementStatus = Literal["proposed", "approved", "rejected"]


class ImprovementCandidate(BaseModel):
    """A proposed prompt/workflow change — data only. Nothing in
    AgentGuard ever deploys a candidate automatically; see
    agentguard/improve/workflow.py."""

    id: str = Field(default_factory=new_run_id)
    source_run_id: str
    workspace_id: str | None = None
    """Isolation boundary (Dashboard V2) — inherited from the source
    run's workspace at proposal time."""
    problem: str
    root_cause_summary: str
    recommendation: str
    candidate_change: dict[str, str] = Field(default_factory=dict)
    """{"before": ..., "after": ...}"""
    expected_benefit: str = ""
    optimizer: str = ""
    """Name of the PromptOptimizer implementation that produced this
    candidate (e.g. "DeterministicTestOptimizer" or "DSPyOptimizer")."""
    real_dspy_optimizer: bool = False
    """False unless a real, configured DSPy optimizer + LM actually ran
    — never silently implied by the mere presence of a `dspy` import."""
    status: ImprovementStatus = "proposed"
    approved_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None


class ImprovementEvaluation(BaseModel):
    """The historical-corpus regression evaluation a candidate was
    validated against before any human approval decision."""

    id: str = Field(default_factory=new_run_id)
    candidate_id: str
    baseline_run_ids: list[str] = Field(default_factory=list)
    candidate_run_ids: list[str] = Field(default_factory=list)
    comparison: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


ProblemStatus = Literal["open", "acknowledged", "fix_proposed", "resolved", "ignored"]
FailureClusterSignal = Literal["trace_exception", "root_cause_explanation"]


class FailureCluster(BaseModel):
    """A cross-run "problem" — many failed runs grouped by a deterministic
    signal (agentguard/reliability/cluster.py). Membership/count/confidence/
    priority are recomputed FRESH from persisted Run/RootCause/TraceStep
    data on every request — same "never cache a derived aggregate"
    principle as BehaviorFingerprint/ToolProfile. Only `status`,
    `linked_candidate_id`, `triaged_by`, `triaged_at` are ever actually
    persisted (agentguard_problems) — they record a human triage decision
    that recomputation must never silently overwrite or lose."""

    cluster_key: str
    workspace_id: str | None = None
    signal: FailureClusterSignal
    exception_type: str | None = None
    code_file: str | None = None
    code_function: str | None = None
    root_cause_explanation: str | None = None
    explanation: str
    count: int
    run_ids: list[str] = Field(default_factory=list)
    representative_run_id: str
    first_seen: datetime
    last_seen: datetime
    confidence: float
    priority_score: float
    priority_factors: dict[str, float] = Field(default_factory=dict)
    priority_weights: dict[str, float] = Field(default_factory=dict)
    status: ProblemStatus = "open"
    linked_candidate_id: str | None = None
    triaged_by: str | None = None
    triaged_at: datetime | None = None


class CIGateResult(BaseModel):
    id: str = Field(default_factory=new_run_id)
    baseline_run_ids: list[str] = Field(default_factory=list)
    candidate_run_ids: list[str] = Field(default_factory=list)
    threshold_pct: float = 0.0
    protected_metrics: list[str] = Field(default_factory=list)
    regressions: dict[str, float] = Field(default_factory=dict)
    result: Literal["pass", "block"] = "pass"
    created_at: datetime = Field(default_factory=_now)


# =====================================================================
# Evaluation Platform (agentguard/evaluation/) — Phase 1: core storage.
# See docs/superpowers-like design doc "AgentGuard Eval Platform" for
# the full architecture. `EvaluationResult` is intentionally distinct
# from the pre-existing `EvalResult` above (that one scores a completed
# Run against its Policy for the Decision Engine; this one scores an
# LLM/agent output against a quality metric for the eval platform —
# same word, different subsystem, kept as separate classes on purpose
# so neither's fields get stretched to cover the other's meaning).
# =====================================================================

EvaluationRunStatus = Literal["running", "complete", "failed"]
SuiteMetricSource = Literal["auto", "app_type", "manual"]


class SuiteMetric(BaseModel):
    """One evaluator entry within an EvaluationSuite."""

    evaluator: str
    """Registry key the EvaluationEngine looks up (e.g.
    "custom.my_metric" or, once the DeepEval adapter ships,
    "deepeval.faithfulness")."""
    threshold: float | None = None
    """When set, a result's `score >= threshold` determines `passed` —
    None leaves `passed` as whatever the evaluator itself reported."""
    recommended_by: SuiteMetricSource = "manual"
    reason: str = ""


class EvaluationSuite(BaseModel):
    """A named, versioned set of metrics to run — append-only version
    chain, same "never mutate a prior version" rule as PolicyDefinition/
    PolicyVersionRecord."""

    id: str = Field(default_factory=new_run_id)
    name: str
    workspace_id: str | None = None
    app_type: str | None = None
    metrics: list[SuiteMetric] = Field(default_factory=list)
    version: int = 1
    created_at: datetime = Field(default_factory=_now)


class EvaluationRun(BaseModel):
    """One execution of an EvaluationSuite against one or more EvalCases
    (built from Run/TraceStep data or a Dataset — Phase 1 only supports
    live Run data; Dataset arrives in a later phase)."""

    id: str = Field(default_factory=new_run_id)
    suite_id: str
    workspace_id: str | None = None
    source_run_ids: list[str] = Field(default_factory=list)
    status: EvaluationRunStatus = "running"
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class EvaluationResult(BaseModel):
    """One (EvaluationRun, source run, metric) scoring row.

    `score`/`confidence`/`cost_usd`/`judge_model` are None — never a
    fabricated number — whenever the metric genuinely could not be
    computed; `available=False` is the explicit signal for that case,
    matching Run.estimated_cost_usd's own "N/A, not a guess" rule.
    """

    id: str = Field(default_factory=new_run_id)
    evaluation_run_id: str
    source_run_id: str | None = None
    source_step_id: str | None = None
    """The specific TraceStep(kind="llm_call") whose output this metric
    scored, when EvalCase.source_step_id was populated (see
    build_eval_case_from_run()) — the anchor
    agentguard.evaluation.diagnose uses to find the real prompt/code
    location behind a failing score. None when the case wasn't built
    from a single identifiable step (e.g. a hand-built EvalCase)."""
    metric: str
    score: float | None = None
    passed: bool | None = None
    available: bool = True
    reason: str = ""
    confidence: float | None = None
    judge_model: str | None = None
    cost_usd: float | None = None
    latency_ms: float = 0.0
    created_at: datetime = Field(default_factory=_now)


class AgentCard(BaseModel):
    """An A2A-style Agent Card — a small, honest identity manifest for
    one registered agent (agentguard/a2a/card.py). AgentGuard genuinely
    only knows an agent's name/workspace/project and (via its runs) its
    latest agent_version; it has no way to derive real "skills" or an
    invocable endpoint, so `capabilities` is empty and `url` is always
    None unless an author explicitly declares them (no fabricated
    data, matching every other reliability metric's own rule)."""

    id: str
    name: str
    description: str | None = None
    version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    capabilities_declared_by_author: bool = False
    url: None = None
    workspace_id: str


# =====================================================================
# Evaluation Platform — Phase 3: Golden Dataset Validation.
# =====================================================================

GoldenStatus = Literal["unvalidated", "VERIFIED", "INCORRECT", "UNSUPPORTED", "AMBIGUOUS", "OUTDATED", "NEEDS_REVIEW"]


class Dataset(BaseModel):
    """A named collection of question/expected-answer examples. Editing
    examples never mutates a DatasetVersion in place — see
    DatasetVersion's own docstring."""

    id: str = Field(default_factory=new_run_id)
    name: str
    workspace_id: str | None = None
    current_version: int = 0
    created_at: datetime = Field(default_factory=_now)


class DatasetVersion(BaseModel):
    """Immutable once created — an edit to a Dataset's examples creates
    a new DatasetVersion, same append-only-version-chain rule as
    EvaluationSuite/PolicyDefinition."""

    id: str = Field(default_factory=new_run_id)
    dataset_id: str
    version: int
    created_at: datetime = Field(default_factory=_now)


class DatasetExample(BaseModel):
    """One question/expected-answer pair, plus the outcome of validating
    that pair against a connected evidence source
    (agentguard/evaluation/dataset/validator.py). `golden_status`
    starts "unvalidated" — never fabricated as VERIFIED until a real
    validation pass has actually run."""

    id: str = Field(default_factory=new_run_id)
    dataset_version_id: str
    question: str
    expected_answer: str
    golden_status: GoldenStatus = "unvalidated"
    validation_confidence: float | None = None
    """Computed deterministically from retrieved evidence (retrieval
    score + agreement across passages) — never the judge's own
    self-reported certainty (see GoldenDatasetValidator)."""
    validation_reason: str = ""
    validated_at: datetime | None = None


class Evidence(BaseModel):
    """One passage retrieved from a DatasetExample's connected evidence
    source (EvidenceSource.retrieve()) — real, sourced text, never
    fabricated or drawn from a judge model's own training knowledge."""

    id: str = Field(default_factory=new_run_id)
    example_id: str
    source_ref: str
    text: str
    retrieval_score: float
    retrieved_at: datetime = Field(default_factory=_now)
    cited_in_verdict: bool = False


# =====================================================================
# Evaluation Platform — Phase 7/8: recommendation + model benchmarking.
# =====================================================================

RecommendationKind = Literal["metric_suite", "model"]


class Recommendation(BaseModel):
    """A stored, auditable recommendation — metric-suite (§10) or
    model (§11) — always carrying the evidence it was computed from,
    never a bare suggestion with no way to check the reasoning."""

    id: str = Field(default_factory=new_run_id)
    workspace_id: str | None = None
    kind: RecommendationKind
    subject_id: str
    """agent_name for kind="metric_suite"; benchmark_id for kind="model"."""
    recommendation: dict[str, Any] = Field(default_factory=dict)
    reasoning: str
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class ModelBenchmark(BaseModel):
    """One "run this suite through N models" benchmark definition."""

    id: str = Field(default_factory=new_run_id)
    workspace_id: str | None = None
    dataset_version_id: str | None = None
    suite_id: str
    models: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class ModelBenchmarkResult(BaseModel):
    """One (benchmark, model, example) row — cost/latency come from the
    real provider call the benchmark made (via whatever `model_call_fn`
    the caller supplied to ModelBenchmarkEngine), never estimated."""

    id: str = Field(default_factory=new_run_id)
    benchmark_id: str
    model: str
    example_id: str | None = None
    evaluation_result_id: str
    cost_usd: float | None = None
    latency_ms: float = 0.0
    tokens_input: int | None = None
    """Real input token count from the provider call (never guessed) —
    consumed by ModelBenchmarkEngine's best_long_context objective."""
    created_at: datetime = Field(default_factory=_now)


# =====================================================================
# Evaluation Platform — Phase 9: evaluator-of-evaluators (judge
# reliability), mirroring ToolProfile/ModelProfile's own "computed
# fresh from real evidence, never a fabricated number" discipline.
# =====================================================================


class JudgeCalibrationExample(BaseModel):
    """One human-labeled (input, output, expected judgment) triple used
    to measure how well a judge model/metric agrees with a real human,
    never a synthetic/self-generated label."""

    id: str = Field(default_factory=new_run_id)
    metric: str
    workspace_id: str | None = None
    case_input: Any = None
    case_actual_output: Any = None
    human_label: dict[str, Any] = Field(default_factory=dict)
    """e.g. {"score": 0.9} or {"passed": True} — whatever shape the
    metric's own EvaluationResult uses for its verdict."""
    created_at: datetime = Field(default_factory=_now)


JobStatus = Literal["pending", "running", "complete", "failed"]


class Job(BaseModel):
    """A durable, queued unit of work — Phase 5's async job queue
    (agentguard/jobs/), for anything (a large dataset validation, a
    model benchmark) that can run for minutes to hours and must
    survive a process restart. Unlike the in-process
    `asyncio.create_task` background trace-write
    (agentguard/tracing/_pending.py, correct for "finish this before the
    run's own finally block returns"), an unclaimed pending Job is
    picked up by the next worker that starts, not silently dropped.
    """

    id: str = Field(default_factory=new_run_id)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "pending"
    attempts: int = 0
    max_attempts: int = 3
    error: str | None = None
    workspace_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class JudgeProfile(BaseModel):
    """A metric/judge's aggregated agreement-with-humans statistics,
    computed fresh from its JudgeCalibrationExample history each time —
    see agentguard/reliability/judge_profile.py."""

    metric: str
    sample_count: int
    agreement_rate: float | None = None
    mean_absolute_error: float | None = None
    reliability: Literal["RELIABLE", "DEGRADED", "UNRELIABLE", "INSUFFICIENT_DATA"]
    min_sample_size: int
