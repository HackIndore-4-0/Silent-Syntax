"""Storage interface.

agentguard.decorator depends only on this ABC, never on asyncpg or SQL
directly — Rule 4's "keep database access behind a repository/storage
interface" applied literally. A future event-pipeline worker (Phase 2+)
can implement the same interface against a queue instead of a direct DB
write without decorator.py changing at all.

Phase 2 adds risk assessments, root causes, and human decisions as
first-class persisted records, alongside Phase 1's runs/spans/policies/
evaluations/decisions.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ..auth.models import (
    AgentRegistration,
    ApiKey,
    PasswordResetToken,
    Project,
    Session,
    User,
    Workspace,
    WorkspaceMembership,
)
from ..models import (
    AuditEvent,
    Checkpoint,
    CIGateResult,
    CounterfactualResult,
    Dataset,
    DatasetExample,
    DatasetVersion,
    Decision,
    EvalResult,
    Evidence,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    HumanDecision,
    ImprovementCandidate,
    ImprovementEvaluation,
    Job,
    JudgeCalibrationExample,
    ModelAlternative,
    ModelBenchmark,
    ModelBenchmarkResult,
    Policy,
    PolicyDefinition,
    PolicyVersionRecord,
    Recommendation,
    RiskAssessment,
    RootCause,
    Run,
    ToolAlternative,
    ToolCallEvent,
    TraceStep,
)


class RunRepository(ABC):
    @abstractmethod
    async def create_run(self, run: Run) -> None: ...

    @abstractmethod
    async def update_run(self, run: Run) -> None: ...

    @abstractmethod
    async def save_span(
        self,
        run: Run,
        name: str,
        trace_id: str,
        span_id: str,
        start_time: datetime,
        end_time: datetime | None,
        attributes: dict[str, Any],
    ) -> None: ...

    @abstractmethod
    async def save_policy(self, run: Run, policy: Policy) -> None: ...

    @abstractmethod
    async def save_evaluation(self, run: Run, result: EvalResult) -> None: ...

    @abstractmethod
    async def save_decision(self, run: Run, decision: Decision) -> None: ...

    @abstractmethod
    async def save_risk_assessment(self, run_id: str, risk: RiskAssessment) -> None: ...

    @abstractmethod
    async def save_root_cause(self, run_id: str, root_cause: RootCause) -> None: ...

    @abstractmethod
    async def save_human_decision(self, human_decision: HumanDecision) -> None:
        """Upsert by human_decision.id — called once to record the
        request as "pending" and again once it resolves. `run_id` lives
        on the HumanDecision itself."""
        ...

    @abstractmethod
    async def set_run_status(self, run_id: str, status: Any) -> None:
        """Narrow status-only update, used by the post-hoc HUMAN
        resolution path (agentguard/human/resolution.py), which only has
        a run_id + outcome, not a full Run object."""
        ...

    @abstractmethod
    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a run plus its policy/evaluations/decisions, dashboard-shaped."""
        ...

    @abstractmethod
    async def list_runs(
        self, limit: int = 50, offset: int = 0, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        """`workspace_id=None` (the default) returns runs regardless of
        owner — preserves exact pre-Dashboard-V2 behavior for the CLI,
        examples, and every existing test. The authenticated dashboard
        API always passes a concrete workspace_id; this is where
        cross-tenant run-list isolation is actually enforced."""
        ...

    @abstractmethod
    async def list_risk_assessments(self, run_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_root_cause(self, run_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_decisions(self, run_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def list_human_decisions(self, run_id: str) -> list[dict[str, Any]]: ...

    # -- Phase 3: checkpoints, audit trail, counterfactuals ----------------

    @abstractmethod
    async def save_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    async def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_audit_event(self, event: AuditEvent) -> None: ...

    @abstractmethod
    async def list_audit_events(self, run_id: str) -> list[dict[str, Any]]:
        """Ordered by `seq` ascending — the order the hash chain was
        built in, required for verify_audit_chain() to replay it
        correctly."""
        ...

    @abstractmethod
    async def save_counterfactual(self, result: CounterfactualResult) -> None: ...

    @abstractmethod
    async def get_counterfactual(self, run_id: str) -> dict[str, Any] | None:
        """Most recent CounterfactualResult recorded for this run, or
        None if counterfactual analysis hasn't been run yet."""
        ...

    # -- Phase 4: tool calls / alternatives ---------------------------------

    @abstractmethod
    async def save_tool_call(self, event: ToolCallEvent) -> None: ...

    @abstractmethod
    async def list_tool_calls(self, tool: str) -> list[dict[str, Any]]:
        """Every recorded ToolCallEvent for `tool`, across all runs —
        the raw evidence ToolProfile aggregates."""
        ...

    @abstractmethod
    async def list_tool_calls_for_run(self, run_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def list_tools(self, workspace_id: str | None = None) -> list[str]:
        """Distinct tool names that have ever recorded a call. `workspace_id`
        filters to tools called from within that workspace's runs only."""
        ...

    # -- TraceStep tracing (agentguard/tracing/: @traceable, wrap_llm_client) --

    @abstractmethod
    async def save_trace_step(self, step: TraceStep) -> None: ...

    @abstractmethod
    async def list_trace_steps_for_run(self, run_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_trace_step(self, step_id: str) -> dict[str, Any] | None:
        """Single-step lookup — used by agentguard.evaluation.diagnose
        to resolve an EvaluationResult.source_step_id back to the real
        TraceStep (its input/prompt, code_file/function/lineno) behind
        a failing score."""
        ...

    # -- LLM Gateway (agentguard/tracing/litellm_wrap.py) ---------------------

    @abstractmethod
    async def list_llm_calls(self, model_name: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        """Every recorded TraceStep(kind="llm_call") for `model_name` —
        the raw evidence ModelProfile aggregates. Mirrors
        list_tool_calls exactly."""
        ...

    @abstractmethod
    async def list_llm_models(self, workspace_id: str | None = None) -> list[str]:
        """Distinct non-null model_name values ever recorded on a
        kind="llm_call" TraceStep. Mirrors list_tools exactly."""
        ...

    @abstractmethod
    async def save_model_alternative(self, alternative: ModelAlternative) -> None:
        """Upsert by `(workspace_id, primary_model)` — persists an
        explicit registration so it survives a process restart
        (litellm_wrap.py's in-process registry stays the live source of
        truth traced_completion() reads from). Mirrors
        save_tool_alternative exactly."""
        ...

    @abstractmethod
    async def list_model_alternatives(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_tool_alternative(self, alternative: ToolAlternative) -> None:
        """Upsert by `(workspace_id, primary)` — persists an explicit
        registration so it survives a process restart
        (agentguard.tools.ToolRegistry is still the in-process source of
        truth call_tool() reads from)."""
        ...

    @abstractmethod
    async def list_tool_alternatives(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    # -- Phase 4: behavior fingerprinting ------------------------------------

    @abstractmethod
    async def list_runs_by_agent(
        self, agent_name: str, limit: int = 200, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Dashboard-shaped run dicts (same shape as get_run()) for every
        run of `agent_name`, most recent first — the raw history
        BehaviorFingerprint aggregates."""
        ...

    # -- Phase 4: policy control portal --------------------------------------

    @abstractmethod
    async def save_policy_definition(self, definition: PolicyDefinition) -> None: ...

    @abstractmethod
    async def get_policy_definition(self, name: str, workspace_id: str | None = None) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_policy_definitions(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_policy_version(self, record: PolicyVersionRecord) -> None: ...

    @abstractmethod
    async def list_policy_versions(self, name: str, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_policy_version(
        self, name: str, version: int, workspace_id: str | None = None
    ) -> dict[str, Any] | None: ...

    # -- Phase 4: auto-improvement --------------------------------------------

    @abstractmethod
    async def save_improvement_candidate(self, candidate: ImprovementCandidate) -> None: ...

    @abstractmethod
    async def get_improvement_candidate(self, candidate_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_improvement_candidates(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_improvement_evaluation(self, evaluation: ImprovementEvaluation) -> None: ...

    @abstractmethod
    async def list_improvement_evaluations(self, candidate_id: str) -> list[dict[str, Any]]: ...

    # -- Cross-run failure clustering (agentguard/reliability/cluster.py) -----
    # Cluster MEMBERSHIP/count/confidence/priority are always recomputed
    # fresh from Run/RootCause/TraceStep data — only a human triage decision
    # is ever persisted here, keyed by the cluster's stable cluster_key.

    @abstractmethod
    async def save_problem_status(
        self,
        workspace_id: str | None,
        cluster_key: str,
        status: str,
        *,
        linked_candidate_id: str | None = None,
        triaged_by: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def get_problem_status(self, workspace_id: str | None, cluster_key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_problem_statuses(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    # -- Phase 4: CI/CD reliability gate ---------------------------------------

    @abstractmethod
    async def save_ci_gate_result(self, result: CIGateResult) -> None: ...

    @abstractmethod
    async def list_ci_gate_results(self, limit: int = 50) -> list[dict[str, Any]]: ...

    # =========================================================================
    # Dashboard V2: multi-user authentication & tenancy
    # =========================================================================

    @abstractmethod
    async def save_user(self, user: User) -> None: ...

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_user_by_email(self, email: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def save_session(self, session: Session) -> None: ...

    @abstractmethod
    async def get_session_by_token_hash(self, token_hash: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def revoke_session(self, session_id: str) -> None: ...

    @abstractmethod
    async def list_sessions_for_user(self, user_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_password_reset_token(self, token: PasswordResetToken) -> None: ...

    @abstractmethod
    async def get_password_reset_token_by_hash(self, token_hash: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def mark_password_reset_token_used(self, token_id: str) -> None: ...

    @abstractmethod
    async def save_workspace(self, workspace: Workspace) -> None: ...

    @abstractmethod
    async def get_workspace(self, workspace_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def save_workspace_membership(self, membership: WorkspaceMembership) -> None: ...

    @abstractmethod
    async def get_membership(self, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        """The authorization primitive: None means `user_id` has no
        access to `workspace_id` at all — every workspace-scoped
        endpoint checks this (or an equivalent already-loaded resource
        ownership check) before returning anything."""
        ...

    @abstractmethod
    async def list_memberships_for_user(self, user_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def list_memberships_for_workspace(self, workspace_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_project(self, project: Project) -> None: ...

    @abstractmethod
    async def get_project(self, project_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_projects_for_workspace(self, workspace_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_agent_registration(self, agent: AgentRegistration) -> None: ...

    @abstractmethod
    async def get_agent_registration(self, workspace_id: str, name: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_agent_registrations(self, workspace_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def update_agent_card(
        self,
        workspace_id: str,
        name: str,
        *,
        description: str | None = None,
        capabilities: list[str] | None = None,
    ) -> None:
        """Sets description/capabilities on an EXISTING AgentRegistration
        row (save_agent_registration()'s own INSERT is DO NOTHING on
        conflict, so it never updates these). Only touches the fields
        actually passed (None means "leave unchanged"), never invents a
        value for the other."""
        ...

    @abstractmethod
    async def save_api_key(self, api_key: ApiKey) -> None: ...

    @abstractmethod
    async def get_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_api_key_by_id(self, key_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_api_keys_for_user(self, user_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def update_api_key_last_used(self, key_id: str) -> None: ...

    # =========================================================================
    # Evaluation Platform (agentguard/evaluation/) — Phase 1: core storage.
    # =========================================================================

    @abstractmethod
    async def save_evaluation_suite(self, suite: EvaluationSuite) -> None:
        """Upsert by id — an existing id is a version bump (caller
        already incremented `.version`), never an in-place mutation of
        what a prior version meant."""
        ...

    @abstractmethod
    async def get_evaluation_suite(self, suite_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_evaluation_suites(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_evaluation_run(self, evaluation_run: EvaluationRun) -> None:
        """Upsert by id — called once to record status="running" and
        again once the run completes/fails, mirroring create_run()/
        update_run()'s two-write pattern but collapsed into one method
        since EvaluationRun has no separate policy/span bookkeeping."""
        ...

    @abstractmethod
    async def get_evaluation_run(self, evaluation_run_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_evaluation_runs(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_evaluation_result(self, result: EvaluationResult) -> None: ...

    @abstractmethod
    async def list_evaluation_results(self, evaluation_run_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_evaluation_result(self, result_id: str) -> dict[str, Any] | None:
        """Single result lookup by id — used by ModelBenchmarkEngine to
        join a ModelBenchmarkResult back to the metric/score it scored."""
        ...

    # =========================================================================
    # Evaluation Platform — Phase 3: Golden Dataset Validation.
    # =========================================================================

    @abstractmethod
    async def save_dataset(self, dataset: Dataset) -> None: ...

    @abstractmethod
    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_datasets(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_dataset_version(self, version: DatasetVersion) -> None: ...

    @abstractmethod
    async def list_dataset_versions(self, dataset_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_dataset_version(self, dataset_id: str, version: int) -> dict[str, Any] | None: ...

    @abstractmethod
    async def save_dataset_example(self, example: DatasetExample) -> None:
        """Upsert by id — used both to create an example and to record
        a validation pass's updated golden_status/confidence/reason."""
        ...

    @abstractmethod
    async def get_dataset_example(self, example_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_dataset_examples(self, dataset_version_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_evidence(self, evidence: Evidence) -> None: ...

    @abstractmethod
    async def list_evidence_for_example(self, example_id: str) -> list[dict[str, Any]]: ...

    # =========================================================================
    # Evaluation Platform — Phase 7/8: recommendation + model benchmarking.
    # =========================================================================

    @abstractmethod
    async def save_recommendation(self, recommendation: Recommendation) -> None: ...

    @abstractmethod
    async def list_recommendations(
        self, kind: str | None = None, workspace_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_model_benchmark(self, benchmark: ModelBenchmark) -> None: ...

    @abstractmethod
    async def get_model_benchmark(self, benchmark_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_model_benchmarks(self, workspace_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def save_model_benchmark_result(self, result: ModelBenchmarkResult) -> None: ...

    @abstractmethod
    async def list_model_benchmark_results(self, benchmark_id: str) -> list[dict[str, Any]]: ...

    # =========================================================================
    # Evaluation Platform — Phase 9: evaluator-of-evaluators (judge reliability).
    # =========================================================================

    @abstractmethod
    async def save_judge_calibration_example(self, example: JudgeCalibrationExample) -> None: ...

    @abstractmethod
    async def list_judge_calibration_examples(
        self, metric: str, workspace_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    # =========================================================================
    # Evaluation Platform — Phase 5: async job queue (agentguard/jobs/).
    # =========================================================================

    @abstractmethod
    async def enqueue_job(self, job: Job) -> None: ...

    @abstractmethod
    async def claim_next_job(self, kinds: list[str] | None = None) -> dict[str, Any] | None:
        """Atomically claims the oldest pending job (status pending ->
        running), optionally restricted to `kinds`. PostgresRunRepository
        does this with a real `FOR UPDATE SKIP LOCKED` query so multiple
        `agentguard worker` processes never claim the same job twice;
        InMemoryRunRepository uses an asyncio.Lock-guarded scan
        (single-process only, consistent with that repository's
        documented scope everywhere else)."""
        ...

    @abstractmethod
    async def complete_job(self, job_id: str) -> None: ...

    @abstractmethod
    async def fail_job(self, job_id: str, error: str, *, retry: bool) -> None:
        """retry=True and attempts < max_attempts -> back to "pending"
        for another claim; otherwise -> terminal "failed"."""
        ...

    @abstractmethod
    async def get_job(self, job_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_jobs(self, status: str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]: ...
