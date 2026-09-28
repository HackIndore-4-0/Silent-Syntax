"""In-memory RunRepository.

Implements the exact same `RunRepository` interface `PostgresRunRepository`
does, so it is a drop-in for two purposes:

1. Tests — the domain flow (decorator -> evaluator -> decision engine ->
   reliability engine -> checkpoint/audit) can be exercised without a
   running Postgres instance.
2. Running the SDK's own examples/demos in an environment with no
   reachable PostgreSQL (see examples/budget_failure_recovery.py and
   docs/EXECUTION_REPORT_PHASE_3.md §1 for why this environment needs
   that — no `docker`, no `AGENTGUARD_DATABASE_URL`). This is the same
   `RunRepository` abstraction `PostgresRunRepository` implements; swap
   one for the other via `agentguard.configure(...)` and nothing else
   in the SDK changes.

Never the *default* repository in production: `agentguard._runtime.get_repository()`
still resolves to `PostgresRunRepository` unless a caller explicitly
`agentguard.configure()`s something else (Rule 4: Postgres is the real
storage layer).

Dashboard V2 note: every `workspace_id=None` default below preserves the
exact pre-Dashboard-V2 (single-tenant) behavior every existing test
relies on — passing a concrete `workspace_id` is what the authenticated
dashboard/API layer does, and is where cross-tenant isolation is
actually enforced (a plain equality filter on real stored data, not a
frontend-only hide).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    new_run_id,
)
from .repository import RunRepository


def _dump_with_duration(run: Run) -> dict[str, Any]:
    data = run.model_dump()
    data["duration_ms"] = run.duration_ms
    return data


class InMemoryRunRepository(RunRepository):
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.policies: dict[str, Policy] = {}
        self.evaluations: dict[str, list[EvalResult]] = {}
        self.decisions: dict[str, list[Decision]] = {}
        self.spans: list[dict[str, Any]] = []
        self.risk_assessments: dict[str, list[RiskAssessment]] = {}
        self.root_causes: dict[str, list[RootCause]] = {}
        self.human_decisions: dict[str, dict[str, HumanDecision]] = {}
        self.checkpoints: dict[str, list[Checkpoint]] = {}
        self.audit_events: dict[str, list[AuditEvent]] = {}
        self.counterfactuals: dict[str, list[CounterfactualResult]] = {}
        # Phase 4
        self.tool_calls: list[ToolCallEvent] = []
        self.trace_steps: list[TraceStep] = []
        self.tool_alternatives: dict[tuple[str | None, str], ToolAlternative] = {}
        self.model_alternatives: dict[tuple[str | None, str], ModelAlternative] = {}
        self.policy_definitions: dict[tuple[str | None, str], PolicyDefinition] = {}
        self.policy_versions: dict[tuple[str | None, str], list[PolicyVersionRecord]] = {}
        self.improvement_candidates: dict[str, ImprovementCandidate] = {}
        self.improvement_evaluations: dict[str, list[ImprovementEvaluation]] = {}
        self.problem_statuses: dict[tuple[str | None, str], dict[str, Any]] = {}
        self.ci_gate_results: list[CIGateResult] = []
        # Dashboard V2: auth & tenancy
        self.users: dict[str, User] = {}
        self.users_by_email: dict[str, str] = {}  # lowercased email -> user_id
        self.sessions: dict[str, Session] = {}
        self.sessions_by_token_hash: dict[str, str] = {}  # token_hash -> session_id
        self.password_reset_tokens: dict[str, PasswordResetToken] = {}
        self.password_reset_by_hash: dict[str, str] = {}
        self.workspaces: dict[str, Workspace] = {}
        self.memberships: dict[tuple[str, str], WorkspaceMembership] = {}  # (workspace_id, user_id)
        self.projects: dict[str, Project] = {}
        self.agent_registrations: dict[tuple[str, str], AgentRegistration] = {}  # (workspace_id, name)
        self.api_keys: dict[str, ApiKey] = {}
        # Evaluation Platform
        self.evaluation_suites: dict[str, EvaluationSuite] = {}
        self.evaluation_runs: dict[str, EvaluationRun] = {}
        self.evaluation_results: dict[str, list[EvaluationResult]] = {}  # keyed by evaluation_run_id
        # Evaluation Platform — Phase 3/7/8/9
        self.datasets: dict[str, Dataset] = {}
        self.dataset_versions: dict[str, list[DatasetVersion]] = {}  # keyed by dataset_id
        self.dataset_examples: dict[str, DatasetExample] = {}
        self.evidence: dict[str, list[Evidence]] = {}  # keyed by example_id
        self.recommendations: dict[str, Recommendation] = {}
        self.model_benchmarks: dict[str, ModelBenchmark] = {}
        self.model_benchmark_results: dict[str, list[ModelBenchmarkResult]] = {}  # keyed by benchmark_id
        self.judge_calibration_examples: dict[str, list[JudgeCalibrationExample]] = {}  # keyed by metric
        # Phase 5: async job queue
        self.jobs: dict[str, Job] = {}
        self._jobs_lock = asyncio.Lock()
        self.api_keys_by_hash: dict[str, str] = {}  # key_hash -> key_id

    async def create_run(self, run: Run) -> None:
        self.runs[run.id] = run.model_copy(deep=True)
        self.policies[run.id] = run.policy
        self.evaluations.setdefault(run.id, [])
        self.decisions.setdefault(run.id, [])
        self.risk_assessments.setdefault(run.id, [])
        self.root_causes.setdefault(run.id, [])
        self.human_decisions.setdefault(run.id, {})
        self.checkpoints.setdefault(run.id, [])
        self.audit_events.setdefault(run.id, [])
        self.counterfactuals.setdefault(run.id, [])

    async def update_run(self, run: Run) -> None:
        self.runs[run.id] = run.model_copy(deep=True)

    async def save_span(self, run, name, trace_id, span_id, start_time, end_time, attributes):
        self.spans.append(
            {
                "run_id": run.id,
                "name": name,
                "trace_id": trace_id,
                "span_id": span_id,
                "start_time": start_time,
                "end_time": end_time,
                "attributes": attributes,
            }
        )

    async def save_policy(self, run: Run, policy: Policy) -> None:
        self.policies[run.id] = policy

    async def save_evaluation(self, run: Run, result: EvalResult) -> None:
        self.evaluations.setdefault(run.id, []).append(result)

    async def save_decision(self, run: Run, decision: Decision) -> None:
        self.decisions.setdefault(run.id, []).append(decision)

    async def save_risk_assessment(self, run_id: str, risk: RiskAssessment) -> None:
        self.risk_assessments.setdefault(run_id, []).append(risk)

    async def save_root_cause(self, run_id: str, root_cause: RootCause) -> None:
        self.root_causes.setdefault(run_id, []).append(root_cause)

    async def save_human_decision(self, human_decision: HumanDecision) -> None:
        self.human_decisions.setdefault(human_decision.run_id, {})[human_decision.id] = human_decision.model_copy(
            deep=True
        )

    async def set_run_status(self, run_id: str, status: Any) -> None:
        run = self.runs.get(run_id)
        if run is not None:
            run.status = status

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        data = _dump_with_duration(run)
        data["evaluations"] = [e.model_dump() for e in self.evaluations.get(run_id, [])]
        data["decisions"] = [d.model_dump() for d in self.decisions.get(run_id, [])]
        data["policy"] = self.policies.get(run_id, Policy()).model_dump()
        data["risk_assessments"] = [r.model_dump() for r in self.risk_assessments.get(run_id, [])]
        root_causes = self.root_causes.get(run_id, [])
        data["root_cause"] = root_causes[0].model_dump() if root_causes else None
        data["human_decisions"] = [h.model_dump() for h in self.human_decisions.get(run_id, {}).values()]
        return data

    async def list_runs(
        self, limit: int = 50, offset: int = 0, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        runs = list(self.runs.values())
        if workspace_id is not None:
            runs = [r for r in runs if r.workspace_id == workspace_id]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return [_dump_with_duration(r) for r in runs[offset : offset + limit]]

    async def list_risk_assessments(self, run_id: str) -> list[dict[str, Any]]:
        return [r.model_dump() for r in self.risk_assessments.get(run_id, [])]

    async def get_root_cause(self, run_id: str) -> dict[str, Any] | None:
        root_causes = self.root_causes.get(run_id, [])
        return root_causes[0].model_dump() if root_causes else None

    async def list_decisions(self, run_id: str) -> list[dict[str, Any]]:
        return [d.model_dump() for d in self.decisions.get(run_id, [])]

    async def list_human_decisions(self, run_id: str) -> list[dict[str, Any]]:
        return [h.model_dump() for h in self.human_decisions.get(run_id, {}).values()]

    # -- Phase 3 -----------------------------------------------------------

    async def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self.checkpoints.setdefault(checkpoint.run_id, []).append(checkpoint.model_copy(deep=True))

    async def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        return [c.model_dump() for c in sorted(self.checkpoints.get(run_id, []), key=lambda c: c.seq)]

    async def save_audit_event(self, event: AuditEvent) -> None:
        self.audit_events.setdefault(event.run_id, []).append(event.model_copy(deep=True))

    async def list_audit_events(self, run_id: str) -> list[dict[str, Any]]:
        return [e.model_dump() for e in sorted(self.audit_events.get(run_id, []), key=lambda e: e.seq)]

    async def save_counterfactual(self, result: CounterfactualResult) -> None:
        self.counterfactuals.setdefault(result.run_id, []).append(result.model_copy(deep=True))

    async def get_counterfactual(self, run_id: str) -> dict[str, Any] | None:
        results = self.counterfactuals.get(run_id, [])
        return results[-1].model_dump() if results else None

    # -- Phase 4: tool calls / alternatives ---------------------------------

    async def save_tool_call(self, event: ToolCallEvent) -> None:
        self.tool_calls.append(event.model_copy(deep=True))

    async def list_tool_calls(self, tool: str) -> list[dict[str, Any]]:
        return [e.model_dump() for e in self.tool_calls if e.tool == tool]

    async def list_tool_calls_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [e.model_dump() for e in self.tool_calls if e.run_id == run_id]

    async def list_tools(self, workspace_id: str | None = None) -> list[str]:
        if workspace_id is None:
            return sorted({e.tool for e in self.tool_calls})
        run_ids = {r.id for r in self.runs.values() if r.workspace_id == workspace_id}
        return sorted({e.tool for e in self.tool_calls if e.run_id in run_ids})

    async def save_trace_step(self, step: TraceStep) -> None:
        self.trace_steps.append(step.model_copy(deep=True))

    async def list_trace_steps_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [s.model_dump() for s in self.trace_steps if s.run_id == run_id]

    async def get_trace_step(self, step_id: str) -> dict[str, Any] | None:
        for s in self.trace_steps:
            if s.id == step_id:
                return s.model_dump()
        return None

    async def list_llm_calls(self, model_name: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        steps = [s for s in self.trace_steps if s.kind == "llm_call" and s.model_name == model_name]
        if workspace_id is not None:
            run_ids = {r.id for r in self.runs.values() if r.workspace_id == workspace_id}
            steps = [s for s in steps if s.run_id in run_ids]
        return [s.model_dump() for s in steps]

    async def list_llm_models(self, workspace_id: str | None = None) -> list[str]:
        steps = [s for s in self.trace_steps if s.kind == "llm_call" and s.model_name is not None]
        if workspace_id is not None:
            run_ids = {r.id for r in self.runs.values() if r.workspace_id == workspace_id}
            steps = [s for s in steps if s.run_id in run_ids]
        return sorted({s.model_name for s in steps})

    async def save_model_alternative(self, alternative: ModelAlternative) -> None:
        self.model_alternatives[(alternative.workspace_id, alternative.primary_model)] = alternative.model_copy(deep=True)

    async def list_model_alternatives(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [a.model_dump() for (ws, _name), a in self.model_alternatives.items() if ws == workspace_id]

    async def save_tool_alternative(self, alternative: ToolAlternative) -> None:
        self.tool_alternatives[(alternative.workspace_id, alternative.primary)] = alternative.model_copy(deep=True)

    async def list_tool_alternatives(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [a.model_dump() for (ws, _name), a in self.tool_alternatives.items() if ws == workspace_id]

    # -- Phase 4: behavior fingerprinting ------------------------------------

    async def list_runs_by_agent(
        self, agent_name: str, limit: int = 200, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        runs = [r for r in self.runs.values() if r.agent_name == agent_name]
        if workspace_id is not None:
            runs = [r for r in runs if r.workspace_id == workspace_id]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        result = []
        for r in runs[:limit]:
            data = await self.get_run(r.id)
            if data is not None:
                result.append(data)
        return result

    # -- Phase 4: policy control portal --------------------------------------

    async def save_policy_definition(self, definition: PolicyDefinition) -> None:
        self.policy_definitions[(definition.workspace_id, definition.name)] = definition.model_copy(deep=True)

    async def get_policy_definition(self, name: str, workspace_id: str | None = None) -> dict[str, Any] | None:
        d = self.policy_definitions.get((workspace_id, name))
        return d.model_dump() if d else None

    async def list_policy_definitions(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [d.model_dump() for (ws, _name), d in self.policy_definitions.items() if ws == workspace_id]

    async def save_policy_version(self, record: PolicyVersionRecord) -> None:
        key = (record.workspace_id, record.policy_name)
        self.policy_versions.setdefault(key, []).append(record.model_copy(deep=True))

    async def list_policy_versions(self, name: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        versions = sorted(self.policy_versions.get((workspace_id, name), []), key=lambda r: r.version)
        return [v.model_dump() for v in versions]

    async def get_policy_version(
        self, name: str, version: int, workspace_id: str | None = None
    ) -> dict[str, Any] | None:
        for v in self.policy_versions.get((workspace_id, name), []):
            if v.version == version:
                return v.model_dump()
        return None

    # -- Phase 4: auto-improvement --------------------------------------------

    async def save_improvement_candidate(self, candidate: ImprovementCandidate) -> None:
        self.improvement_candidates[candidate.id] = candidate.model_copy(deep=True)

    async def get_improvement_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        c = self.improvement_candidates.get(candidate_id)
        return c.model_dump() if c else None

    async def list_improvement_candidates(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        candidates = self.improvement_candidates.values()
        if workspace_id is not None:
            candidates = [c for c in candidates if c.workspace_id == workspace_id]
        return [c.model_dump() for c in candidates]

    async def save_improvement_evaluation(self, evaluation: ImprovementEvaluation) -> None:
        self.improvement_evaluations.setdefault(evaluation.candidate_id, []).append(evaluation.model_copy(deep=True))

    async def list_improvement_evaluations(self, candidate_id: str) -> list[dict[str, Any]]:
        return [e.model_dump() for e in self.improvement_evaluations.get(candidate_id, [])]

    # -- Cross-run failure clustering -----------------------------------------

    async def save_problem_status(
        self,
        workspace_id: str | None,
        cluster_key: str,
        status: str,
        *,
        linked_candidate_id: str | None = None,
        triaged_by: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        key = (workspace_id, cluster_key)
        existing = self.problem_statuses.get(key, {})
        self.problem_statuses[key] = {
            "id": existing.get("id", new_run_id()),
            "workspace_id": workspace_id,
            "cluster_key": cluster_key,
            "status": status,
            "linked_candidate_id": linked_candidate_id,
            "triaged_by": triaged_by,
            "triaged_at": now,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }

    async def get_problem_status(self, workspace_id: str | None, cluster_key: str) -> dict[str, Any] | None:
        return self.problem_statuses.get((workspace_id, cluster_key))

    async def list_problem_statuses(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [v for (ws, _key), v in self.problem_statuses.items() if ws == workspace_id]

    # -- Phase 4: CI/CD reliability gate ---------------------------------------

    async def save_ci_gate_result(self, result: CIGateResult) -> None:
        self.ci_gate_results.append(result.model_copy(deep=True))

    async def list_ci_gate_results(self, limit: int = 50) -> list[dict[str, Any]]:
        ordered = sorted(self.ci_gate_results, key=lambda r: r.created_at, reverse=True)
        return [r.model_dump() for r in ordered[:limit]]

    # =========================================================================
    # Dashboard V2: multi-user authentication & tenancy
    # =========================================================================

    async def save_user(self, user: User) -> None:
        self.users[user.id] = user.model_copy(deep=True)
        self.users_by_email[user.email.lower()] = user.id

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        u = self.users.get(user_id)
        return u.model_dump() if u else None

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        user_id = self.users_by_email.get(email.lower())
        if user_id is None:
            return None
        return await self.get_user_by_id(user_id)

    async def save_session(self, session: Session) -> None:
        self.sessions[session.id] = session.model_copy(deep=True)
        self.sessions_by_token_hash[session.token_hash] = session.id

    async def get_session_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        session_id = self.sessions_by_token_hash.get(token_hash)
        if session_id is None:
            return None
        s = self.sessions.get(session_id)
        return s.model_dump() if s else None

    async def revoke_session(self, session_id: str) -> None:
        from datetime import datetime, timezone

        s = self.sessions.get(session_id)
        if s is not None:
            s.revoked_at = datetime.now(timezone.utc)

    async def list_sessions_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [s.model_dump() for s in self.sessions.values() if s.user_id == user_id]

    async def save_password_reset_token(self, token: PasswordResetToken) -> None:
        self.password_reset_tokens[token.id] = token.model_copy(deep=True)
        self.password_reset_by_hash[token.token_hash] = token.id

    async def get_password_reset_token_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        token_id = self.password_reset_by_hash.get(token_hash)
        if token_id is None:
            return None
        t = self.password_reset_tokens.get(token_id)
        return t.model_dump() if t else None

    async def mark_password_reset_token_used(self, token_id: str) -> None:
        from datetime import datetime, timezone

        t = self.password_reset_tokens.get(token_id)
        if t is not None:
            t.used_at = datetime.now(timezone.utc)

    async def save_workspace(self, workspace: Workspace) -> None:
        self.workspaces[workspace.id] = workspace.model_copy(deep=True)

    async def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        w = self.workspaces.get(workspace_id)
        return w.model_dump() if w else None

    async def save_workspace_membership(self, membership: WorkspaceMembership) -> None:
        self.memberships[(membership.workspace_id, membership.user_id)] = membership.model_copy(deep=True)

    async def get_membership(self, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        m = self.memberships.get((workspace_id, user_id))
        return m.model_dump() if m else None

    async def list_memberships_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [m.model_dump() for (_ws, uid), m in self.memberships.items() if uid == user_id]

    async def list_memberships_for_workspace(self, workspace_id: str) -> list[dict[str, Any]]:
        return [m.model_dump() for (ws, _uid), m in self.memberships.items() if ws == workspace_id]

    async def save_project(self, project: Project) -> None:
        self.projects[project.id] = project.model_copy(deep=True)

    async def get_project(self, project_id: str) -> dict[str, Any] | None:
        p = self.projects.get(project_id)
        return p.model_dump() if p else None

    async def list_projects_for_workspace(self, workspace_id: str) -> list[dict[str, Any]]:
        return [p.model_dump() for p in self.projects.values() if p.workspace_id == workspace_id]

    async def save_agent_registration(self, agent: AgentRegistration) -> None:
        self.agent_registrations[(agent.workspace_id, agent.name)] = agent.model_copy(deep=True)

    async def get_agent_registration(self, workspace_id: str, name: str) -> dict[str, Any] | None:
        a = self.agent_registrations.get((workspace_id, name))
        return a.model_dump() if a else None

    async def list_agent_registrations(self, workspace_id: str) -> list[dict[str, Any]]:
        return [a.model_dump() for (ws, _name), a in self.agent_registrations.items() if ws == workspace_id]

    async def update_agent_card(
        self,
        workspace_id: str,
        name: str,
        *,
        description: str | None = None,
        capabilities: list[str] | None = None,
    ) -> None:
        agent = self.agent_registrations.get((workspace_id, name))
        if agent is None:
            return
        if description is not None:
            agent.description = description
        if capabilities is not None:
            agent.capabilities = list(capabilities)

    async def save_api_key(self, api_key: ApiKey) -> None:
        self.api_keys[api_key.id] = api_key.model_copy(deep=True)
        self.api_keys_by_hash[api_key.key_hash] = api_key.id

    async def get_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        key_id = self.api_keys_by_hash.get(key_hash)
        if key_id is None:
            return None
        k = self.api_keys.get(key_id)
        return k.model_dump() if k else None

    async def get_api_key_by_id(self, key_id: str) -> dict[str, Any] | None:
        k = self.api_keys.get(key_id)
        return k.model_dump() if k else None

    async def list_api_keys_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [k.model_dump() for k in self.api_keys.values() if k.user_id == user_id]

    async def update_api_key_last_used(self, key_id: str) -> None:
        from datetime import datetime, timezone

        k = self.api_keys.get(key_id)
        if k is not None:
            k.last_used_at = datetime.now(timezone.utc)

    # -- Evaluation Platform ---------------------------------------------------

    async def save_evaluation_suite(self, suite: EvaluationSuite) -> None:
        self.evaluation_suites[suite.id] = suite.model_copy(deep=True)

    async def get_evaluation_suite(self, suite_id: str) -> dict[str, Any] | None:
        s = self.evaluation_suites.get(suite_id)
        return s.model_dump() if s else None

    async def list_evaluation_suites(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        suites = self.evaluation_suites.values()
        if workspace_id is not None:
            suites = [s for s in suites if s.workspace_id == workspace_id]
        return [s.model_dump() for s in suites]

    async def save_evaluation_run(self, evaluation_run: EvaluationRun) -> None:
        self.evaluation_runs[evaluation_run.id] = evaluation_run.model_copy(deep=True)

    async def get_evaluation_run(self, evaluation_run_id: str) -> dict[str, Any] | None:
        r = self.evaluation_runs.get(evaluation_run_id)
        return r.model_dump() if r else None

    async def list_evaluation_runs(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        runs = self.evaluation_runs.values()
        if workspace_id is not None:
            runs = [r for r in runs if r.workspace_id == workspace_id]
        return [r.model_dump() for r in runs]

    async def save_evaluation_result(self, result: EvaluationResult) -> None:
        self.evaluation_results.setdefault(result.evaluation_run_id, []).append(result.model_copy(deep=True))

    async def list_evaluation_results(self, evaluation_run_id: str) -> list[dict[str, Any]]:
        return [r.model_dump() for r in self.evaluation_results.get(evaluation_run_id, [])]

    async def get_evaluation_result(self, result_id: str) -> dict[str, Any] | None:
        for results in self.evaluation_results.values():
            for r in results:
                if r.id == result_id:
                    return r.model_dump()
        return None

    # -- Evaluation Platform — Phase 3: Golden Dataset Validation ---------------

    async def save_dataset(self, dataset: Dataset) -> None:
        self.datasets[dataset.id] = dataset.model_copy(deep=True)

    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        d = self.datasets.get(dataset_id)
        return d.model_dump() if d else None

    async def list_datasets(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        datasets = self.datasets.values()
        if workspace_id is not None:
            datasets = [d for d in datasets if d.workspace_id == workspace_id]
        return [d.model_dump() for d in datasets]

    async def save_dataset_version(self, version: DatasetVersion) -> None:
        self.dataset_versions.setdefault(version.dataset_id, []).append(version.model_copy(deep=True))

    async def list_dataset_versions(self, dataset_id: str) -> list[dict[str, Any]]:
        versions = sorted(self.dataset_versions.get(dataset_id, []), key=lambda v: v.version)
        return [v.model_dump() for v in versions]

    async def get_dataset_version(self, dataset_id: str, version: int) -> dict[str, Any] | None:
        for v in self.dataset_versions.get(dataset_id, []):
            if v.version == version:
                return v.model_dump()
        return None

    async def save_dataset_example(self, example: DatasetExample) -> None:
        self.dataset_examples[example.id] = example.model_copy(deep=True)

    async def get_dataset_example(self, example_id: str) -> dict[str, Any] | None:
        e = self.dataset_examples.get(example_id)
        return e.model_dump() if e else None

    async def list_dataset_examples(self, dataset_version_id: str) -> list[dict[str, Any]]:
        return [
            e.model_dump() for e in self.dataset_examples.values() if e.dataset_version_id == dataset_version_id
        ]

    async def save_evidence(self, evidence: Evidence) -> None:
        self.evidence.setdefault(evidence.example_id, []).append(evidence.model_copy(deep=True))

    async def list_evidence_for_example(self, example_id: str) -> list[dict[str, Any]]:
        return [e.model_dump() for e in self.evidence.get(example_id, [])]

    # -- Evaluation Platform — Phase 7/8: recommendation + benchmarking --------

    async def save_recommendation(self, recommendation: Recommendation) -> None:
        self.recommendations[recommendation.id] = recommendation.model_copy(deep=True)

    async def list_recommendations(
        self, kind: str | None = None, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        recs = self.recommendations.values()
        if kind is not None:
            recs = [r for r in recs if r.kind == kind]
        if workspace_id is not None:
            recs = [r for r in recs if r.workspace_id == workspace_id]
        return [r.model_dump() for r in recs]

    async def get_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        r = self.recommendations.get(recommendation_id)
        return r.model_dump() if r else None

    async def update_recommendation_status(
        self, recommendation_id: str, status: str, decided_by: str, decided_at: datetime
    ) -> dict[str, Any] | None:
        r = self.recommendations.get(recommendation_id)
        if r is None:
            return None
        updated = r.model_copy(update={"status": status, "decided_by": decided_by, "decided_at": decided_at})
        self.recommendations[recommendation_id] = updated
        return updated.model_dump()

    async def save_model_benchmark(self, benchmark: ModelBenchmark) -> None:
        self.model_benchmarks[benchmark.id] = benchmark.model_copy(deep=True)

    async def get_model_benchmark(self, benchmark_id: str) -> dict[str, Any] | None:
        b = self.model_benchmarks.get(benchmark_id)
        return b.model_dump() if b else None

    async def list_model_benchmarks(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        benchmarks = self.model_benchmarks.values()
        if workspace_id is not None:
            benchmarks = [b for b in benchmarks if b.workspace_id == workspace_id]
        return sorted((b.model_dump() for b in benchmarks), key=lambda b: b["created_at"], reverse=True)

    async def save_model_benchmark_result(self, result: ModelBenchmarkResult) -> None:
        self.model_benchmark_results.setdefault(result.benchmark_id, []).append(result.model_copy(deep=True))

    async def list_model_benchmark_results(self, benchmark_id: str) -> list[dict[str, Any]]:
        return [r.model_dump() for r in self.model_benchmark_results.get(benchmark_id, [])]

    # -- Evaluation Platform — Phase 9: evaluator-of-evaluators -----------------

    async def save_judge_calibration_example(self, example: JudgeCalibrationExample) -> None:
        self.judge_calibration_examples.setdefault(example.metric, []).append(example.model_copy(deep=True))

    async def list_judge_calibration_examples(
        self, metric: str, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        examples = self.judge_calibration_examples.get(metric, [])
        if workspace_id is not None:
            examples = [e for e in examples if e.workspace_id == workspace_id]
        return [e.model_dump() for e in examples]

    # -- Evaluation Platform — Phase 5: async job queue -------------------------

    async def enqueue_job(self, job: Job) -> None:
        self.jobs[job.id] = job.model_copy(deep=True)

    async def claim_next_job(self, kinds: list[str] | None = None) -> dict[str, Any] | None:
        async with self._jobs_lock:
            candidates = [
                j for j in self.jobs.values()
                if j.status == "pending" and (kinds is None or j.kind in kinds)
            ]
            if not candidates:
                return None
            job = min(candidates, key=lambda j: j.created_at)
            job.status = "running"
            job.updated_at = datetime.now(timezone.utc)
            return job.model_dump()

    async def complete_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is not None:
            job.status = "complete"
            job.updated_at = datetime.now(timezone.utc)

    async def fail_job(self, job_id: str, error: str, *, retry: bool) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        job.attempts += 1
        job.error = error
        job.updated_at = datetime.now(timezone.utc)
        job.status = "pending" if (retry and job.attempts < job.max_attempts) else "failed"

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return job.model_dump() if job else None

    async def list_jobs(self, status: str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
        jobs = self.jobs.values()
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        if workspace_id is not None:
            jobs = [j for j in jobs if j.workspace_id == workspace_id]
        return [j.model_dump() for j in jobs]
