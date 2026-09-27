"""PostgreSQL implementation of RunRepository, using asyncpg.

Phase 1 opened one connection pool per process and wrote directly,
synchronously with respect to the caller (there is no event pipeline
yet). Phase 2 keeps that shape for every table except the LLM Judge's
result, which decorator.py writes from a background asyncio task (see
decorator.py's module docstring) — this class does not know or care
which caller is synchronous vs. background; it is just a repository.

JSONB columns are passed/read as JSON text (json.dumps/json.loads)
rather than through an asyncpg type codec, so the encode/decode point
is explicit and easy to follow without a live database to test against.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

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

DEFAULT_DATABASE_URL = "postgresql://agentguard:agentguard@localhost:5432/agentguard"
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _decode_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds() * 1000


class PostgresRunRepository(RunRepository):
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._pool_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_env(cls) -> "PostgresRunRepository":
        return cls(os.environ.get("AGENTGUARD_DATABASE_URL", DEFAULT_DATABASE_URL))

    async def _get_pool(self) -> asyncpg.Pool:
        """Recreates the pool whenever the running event loop has
        changed since it was created. asyncpg pools/connections are
        bound to the loop that created them, but a sync `@monitor`
        call (agentguard/decorator.py's sync_wrapper) and
        `AgentGuard.__init__`'s key resolution (agentguard/client.py)
        each drive their own `asyncio.run()` — a brand-new loop every
        call — so a repository instance reused across several such
        calls (the normal case: one long-lived process, many sync
        agent invocations) would otherwise crash on the second call
        with `InterfaceError: cannot perform operation: another
        operation is in progress`, since the first pool's connections
        are bound to a now-closed loop. The abandoned pool is simply
        dropped rather than awaited-closed: its loop is already closed
        by the time this notices, so it cannot be closed cleanly."""
        loop = asyncio.get_running_loop()
        if self._pool is None or self._pool_loop is not loop:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            self._pool_loop = loop
        return self._pool

    async def init_schema(self) -> None:
        """Apply agentguard/storage/migrations/*.sql in filename order,
        tracking which ones have already run in
        `agentguard_schema_migrations` so re-running init-db is a no-op
        for migrations already applied. Every migration file is itself
        idempotent (CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS),
        so this is safe even the very first time against a database that
        was hand-seeded with an older schema.sql.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agentguard_schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            applied = {r["filename"] for r in await conn.fetch("SELECT filename FROM agentguard_schema_migrations")}
            for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
                if path.name in applied:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute(
                        "INSERT INTO agentguard_schema_migrations (filename) VALUES ($1)", path.name
                    )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def create_run(self, run: Run) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_runs
                (id, agent_name, task, status, started_at, finished_at,
                 initial_state, final_state, exception_type, exception_message,
                 trace_id, span_id, retry_count, replan_count, actions,
                 parent_run_id, recovery_checkpoint_id, agent_version,
                 workspace_id, project_id, agent_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11, $12, $13, $14, $15::jsonb,
                    $16, $17, $18, $19, $20, $21)
            """,
            run.id,
            run.agent_name,
            run.task,
            run.status.value,
            run.started_at,
            run.finished_at,
            json.dumps(run.initial_state, default=str),
            json.dumps(run.final_state, default=str) if run.final_state is not None else None,
            run.exception_type,
            run.exception_message,
            run.trace_id,
            run.span_id,
            run.retry_count,
            run.replan_count,
            json.dumps(run.actions, default=str),
            run.parent_run_id,
            run.recovery_checkpoint_id,
            run.agent_version,
            run.workspace_id,
            run.project_id,
            run.agent_id,
        )
        await self.save_policy(run, run.policy)

    async def update_run(self, run: Run) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            UPDATE agentguard_runs SET
                status = $2, finished_at = $3, final_state = $4::jsonb,
                exception_type = $5, exception_message = $6,
                trace_id = $7, span_id = $8, retry_count = $9, replan_count = $10,
                actions = $11::jsonb, model_name = $12, tokens_input = $13,
                tokens_output = $14, estimated_cost_usd = $15
            WHERE id = $1
            """,
            run.id,
            run.status.value,
            run.finished_at,
            json.dumps(run.final_state, default=str) if run.final_state is not None else None,
            run.exception_type,
            run.exception_message,
            run.trace_id,
            run.span_id,
            run.retry_count,
            run.replan_count,
            json.dumps(run.actions, default=str),
            run.model_name,
            run.tokens_input,
            run.tokens_output,
            run.estimated_cost_usd,
        )

    async def save_span(
        self,
        run: Run,
        name: str,
        trace_id: str,
        span_id: str,
        start_time: datetime,
        end_time: datetime | None,
        attributes: dict[str, Any],
    ) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_spans
                (run_id, name, trace_id, span_id, parent_span_id,
                 start_time, end_time, attributes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            run.id,
            name,
            trace_id,
            span_id,
            None,
            start_time,
            end_time,
            json.dumps(attributes, default=str),
        )

    async def save_policy(self, run: Run, policy: Policy) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_policies
                (run_id, max_cost, require_approval, forbidden_actions,
                 default_on_uncertain, retry_limit, on_retry_exhausted,
                 max_replans, human_timeout_s, version, on_tool_exhausted)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7, $8, $9, $10, $11)
            """,
            run.id,
            policy.max_cost,
            json.dumps(policy.require_approval, default=str),
            json.dumps(policy.forbidden_actions, default=str),
            policy.default_on_uncertain,
            policy.retry_limit,
            policy.on_retry_exhausted,
            policy.max_replans,
            policy.human_timeout_s,
            policy.version,
            policy.on_tool_exhausted,
        )

    async def save_evaluation(self, run: Run, result: EvalResult) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_evaluations
                (run_id, evaluator, passed, score, label, confidence, reason, evidence)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            run.id,
            result.evaluator,
            result.passed,
            result.score,
            result.label,
            result.confidence,
            result.reason,
            json.dumps(result.evidence, default=str),
        )

    async def save_decision(self, run: Run, decision: Decision) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_decisions
                (run_id, outcome, reason, evidence, risk_score, confidence, retry_count, replan_count)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
            """,
            run.id,
            decision.outcome.value,
            decision.reason,
            json.dumps({**decision.evidence, "policy_findings": [f.model_dump() for f in decision.policy_findings]}, default=str),
            decision.risk_score,
            decision.confidence,
            decision.retry_count,
            decision.replan_count,
        )

    async def save_risk_assessment(self, run_id: str, risk: RiskAssessment) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_risk_assessments
                (run_id, action, risk_score, impact, confidence, factors, weights, explanation)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
            """,
            run_id,
            risk.action,
            risk.risk_score,
            risk.impact,
            risk.confidence,
            json.dumps(risk.factors, default=str),
            json.dumps(risk.weights, default=str),
            risk.explanation,
        )

    async def save_root_cause(self, run_id: str, root_cause: RootCause) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_root_causes
                (run_id, earliest_deviation, expected, observed, confidence, explanation, evidence)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7::jsonb)
            """,
            run_id,
            root_cause.earliest_deviation,
            json.dumps(root_cause.expected, default=str),
            json.dumps(root_cause.observed, default=str),
            root_cause.confidence,
            root_cause.explanation,
            json.dumps(root_cause.evidence, default=str),
        )

    async def save_human_decision(self, human_decision: HumanDecision) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_human_decisions
                (id, run_id, action, decision, risk_score, confidence, reason,
                 evidence, status, timeout_s, requested_at, resolved_at, resolved_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13)
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                resolved_at = EXCLUDED.resolved_at,
                resolved_by = EXCLUDED.resolved_by
            """,
            human_decision.id,
            human_decision.run_id,
            human_decision.action,
            human_decision.decision.value,
            human_decision.risk_score,
            human_decision.confidence,
            human_decision.reason,
            json.dumps(human_decision.evidence, default=str),
            human_decision.status,
            human_decision.timeout_s,
            human_decision.requested_at,
            human_decision.resolved_at,
            human_decision.resolved_by,
        )

    async def set_run_status(self, run_id: str, status: Any) -> None:
        pool = await self._get_pool()
        value = status.value if hasattr(status, "value") else status
        await pool.execute("UPDATE agentguard_runs SET status = $2 WHERE id = $1", run_id, value)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_runs WHERE id = $1", run_id)
        if row is None:
            return None

        result = dict(row)
        result["initial_state"] = _decode_jsonb(result.get("initial_state"))
        result["final_state"] = _decode_jsonb(result.get("final_state"))
        result["actions"] = _decode_jsonb(result.get("actions")) or []
        result["duration_ms"] = _duration_ms(result.get("started_at"), result.get("finished_at"))

        evaluations = await pool.fetch(
            """
            SELECT evaluator, passed, score, label, confidence, reason, evidence, created_at
            FROM agentguard_evaluations WHERE run_id = $1 ORDER BY created_at
            """,
            run_id,
        )
        decisions = await pool.fetch(
            """
            SELECT outcome, reason, evidence, risk_score, confidence, retry_count, replan_count, created_at
            FROM agentguard_decisions WHERE run_id = $1 ORDER BY created_at
            """,
            run_id,
        )
        policies = await pool.fetch(
            """
            SELECT max_cost, require_approval, forbidden_actions, default_on_uncertain,
                   retry_limit, on_retry_exhausted, max_replans, human_timeout_s, version,
                   on_tool_exhausted, created_at
            FROM agentguard_policies WHERE run_id = $1 ORDER BY created_at
            """,
            run_id,
        )

        result["evaluations"] = [
            {**dict(e), "evidence": _decode_jsonb(dict(e)["evidence"])} for e in evaluations
        ]
        result["decisions"] = [
            {**dict(d), "evidence": _decode_jsonb(dict(d)["evidence"])} for d in decisions
        ]
        if policies:
            policy = dict(policies[0])
            policy["require_approval"] = _decode_jsonb(policy["require_approval"])
            policy["forbidden_actions"] = _decode_jsonb(policy["forbidden_actions"])
            result["policy"] = policy
        else:
            result["policy"] = None

        result["risk_assessments"] = await self.list_risk_assessments(run_id)
        result["root_cause"] = await self.get_root_cause(run_id)
        result["human_decisions"] = await self.list_human_decisions(run_id)
        return result

    # -- Phase 3 -----------------------------------------------------------

    async def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_checkpoints
                (id, run_id, label, seq, state_hash, state, valid, created_at)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
            """,
            checkpoint.id,
            checkpoint.run_id,
            checkpoint.label,
            checkpoint.seq,
            checkpoint.state_hash,
            json.dumps(checkpoint.state, default=str),
            checkpoint.valid,
            checkpoint.created_at,
        )

    async def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id, run_id, label, seq, state_hash, state, valid, created_at
            FROM agentguard_checkpoints WHERE run_id = $1 ORDER BY seq
            """,
            run_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            d["state"] = _decode_jsonb(d["state"])
            result.append(d)
        return result

    async def save_audit_event(self, event: AuditEvent) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_audit_events
                (id, run_id, seq, event_type, payload, previous_hash, event_hash, policy_version, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            """,
            event.id,
            event.run_id,
            event.seq,
            event.event_type,
            json.dumps(event.payload, default=str),
            event.previous_hash,
            event.event_hash,
            event.policy_version,
            event.created_at,
        )

    async def list_audit_events(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id, run_id, seq, event_type, payload, previous_hash, event_hash, policy_version, created_at
            FROM agentguard_audit_events WHERE run_id = $1 ORDER BY seq
            """,
            run_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = _decode_jsonb(d["payload"])
            result.append(d)
        return result

    async def save_counterfactual(self, result: CounterfactualResult) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_counterfactuals
                (id, run_id, source_checkpoint_id, actual_path, counterfactual_path,
                 altered_state, result, comparison, confidence, created_at)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8::jsonb, $9, $10)
            """,
            result.id,
            result.run_id,
            result.source_checkpoint_id,
            json.dumps(result.actual_path, default=str),
            json.dumps(result.counterfactual_path, default=str),
            json.dumps(result.altered_state, default=str),
            result.result,
            json.dumps(result.comparison, default=str),
            result.confidence,
            result.created_at,
        )

    async def get_counterfactual(self, run_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT id, run_id, source_checkpoint_id, actual_path, counterfactual_path,
                   altered_state, result, comparison, confidence, created_at
            FROM agentguard_counterfactuals WHERE run_id = $1 ORDER BY created_at DESC LIMIT 1
            """,
            run_id,
        )
        if row is None:
            return None
        d = dict(row)
        d["actual_path"] = _decode_jsonb(d["actual_path"])
        d["counterfactual_path"] = _decode_jsonb(d["counterfactual_path"])
        d["altered_state"] = _decode_jsonb(d["altered_state"])
        d["comparison"] = _decode_jsonb(d["comparison"])
        return d

    async def list_runs(
        self, limit: int = 50, offset: int = 0, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT r.id, r.agent_name, r.task, r.status, r.started_at, r.finished_at,
                   r.retry_count, r.replan_count, r.workspace_id, r.project_id,
                   p.max_cost,
                   d.outcome AS decision_outcome,
                   d.risk_score AS decision_risk_score,
                   e.passed AS constraint_passed
            FROM agentguard_runs r
            LEFT JOIN agentguard_policies p ON p.run_id = r.id
            LEFT JOIN LATERAL (
                SELECT outcome, risk_score FROM agentguard_decisions
                WHERE run_id = r.id ORDER BY created_at DESC LIMIT 1
            ) d ON true
            LEFT JOIN LATERAL (
                SELECT passed FROM agentguard_evaluations
                WHERE run_id = r.id AND evaluator = 'constraint_adherence'
                ORDER BY created_at DESC LIMIT 1
            ) e ON true
            WHERE ($3::text IS NULL OR r.workspace_id = $3)
            ORDER BY r.started_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
            workspace_id,
        )
        return [dict(r) for r in rows]

    async def list_risk_assessments(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT action, risk_score, impact, confidence, factors, weights, explanation, created_at
            FROM agentguard_risk_assessments WHERE run_id = $1 ORDER BY created_at
            """,
            run_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            d["factors"] = _decode_jsonb(d["factors"])
            d["weights"] = _decode_jsonb(d["weights"])
            result.append(d)
        return result

    async def get_root_cause(self, run_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT earliest_deviation, expected, observed, confidence, explanation, evidence, created_at
            FROM agentguard_root_causes WHERE run_id = $1 ORDER BY created_at LIMIT 1
            """,
            run_id,
        )
        if row is None:
            return None
        d = dict(row)
        d["expected"] = _decode_jsonb(d["expected"])
        d["observed"] = _decode_jsonb(d["observed"])
        d["evidence"] = _decode_jsonb(d["evidence"])
        return d

    async def list_decisions(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT outcome, reason, evidence, risk_score, confidence, retry_count, replan_count, created_at
            FROM agentguard_decisions WHERE run_id = $1 ORDER BY created_at
            """,
            run_id,
        )
        return [{**dict(r), "evidence": _decode_jsonb(dict(r)["evidence"])} for r in rows]

    async def list_human_decisions(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id, action, decision, risk_score, confidence, reason, evidence,
                   status, timeout_s, requested_at, resolved_at, resolved_by
            FROM agentguard_human_decisions WHERE run_id = $1 ORDER BY requested_at
            """,
            run_id,
        )
        return [{**dict(r), "evidence": _decode_jsonb(dict(r)["evidence"])} for r in rows]

    # -- Phase 4: tool calls / alternatives ---------------------------------

    async def save_tool_call(self, event: ToolCallEvent) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_tool_calls
                (id, run_id, tool, attempt, outcome, duplicate, is_fallback, latency_ms, error, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            event.id,
            event.run_id,
            event.tool,
            event.attempt,
            event.outcome,
            event.duplicate,
            event.is_fallback,
            event.latency_ms,
            event.error,
            event.created_at,
        )

    async def list_tool_calls(self, tool: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id, run_id, tool, attempt, outcome, duplicate, is_fallback, latency_ms, error, created_at
            FROM agentguard_tool_calls WHERE tool = $1 ORDER BY created_at
            """,
            tool,
        )
        return [dict(r) for r in rows]

    async def list_tool_calls_for_run(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id, run_id, tool, attempt, outcome, duplicate, is_fallback, latency_ms, error, created_at
            FROM agentguard_tool_calls WHERE run_id = $1 ORDER BY created_at
            """,
            run_id,
        )
        return [dict(r) for r in rows]

    async def list_tools(self, workspace_id: str | None = None) -> list[str]:
        pool = await self._get_pool()
        if workspace_id is None:
            rows = await pool.fetch("SELECT DISTINCT tool FROM agentguard_tool_calls ORDER BY tool")
        else:
            rows = await pool.fetch(
                """
                SELECT DISTINCT tc.tool FROM agentguard_tool_calls tc
                JOIN agentguard_runs r ON r.id = tc.run_id
                WHERE r.workspace_id = $1
                ORDER BY tc.tool
                """,
                workspace_id,
            )
        return [r["tool"] for r in rows]

    # -- TraceStep tracing (agentguard/tracing/) ----------------------------

    async def save_trace_step(self, step: TraceStep) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_trace_steps
                (id, run_id, parent_step_id, kind, name, input, output, outcome,
                 latency_ms, exception_type, exception_message, traceback_text,
                 code_file, code_function, code_lineno, tokens_input, tokens_output,
                 cost_usd, model_name, created_at)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18, $19, $20)
            """,
            step.id,
            step.run_id,
            step.parent_step_id,
            step.kind,
            step.name,
            json.dumps(step.input, default=str),
            json.dumps(step.output, default=str) if step.output is not None else None,
            step.outcome,
            step.latency_ms,
            step.exception_type,
            step.exception_message,
            step.traceback_text,
            step.code_file,
            step.code_function,
            step.code_lineno,
            step.tokens_input,
            step.tokens_output,
            step.cost_usd,
            step.model_name,
            step.created_at,
        )

    async def list_trace_steps_for_run(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id, run_id, parent_step_id, kind, name, input, output, outcome,
                   latency_ms, exception_type, exception_message, traceback_text,
                   code_file, code_function, code_lineno, tokens_input, tokens_output,
                   cost_usd, model_name, created_at
            FROM agentguard_trace_steps WHERE run_id = $1 ORDER BY created_at
            """,
            run_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            d["input"] = _decode_jsonb(d["input"])
            d["output"] = _decode_jsonb(d["output"])
            result.append(d)
        return result

    async def get_trace_step(self, step_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT id, run_id, parent_step_id, kind, name, input, output, outcome,
                   latency_ms, exception_type, exception_message, traceback_text,
                   code_file, code_function, code_lineno, tokens_input, tokens_output,
                   cost_usd, model_name, created_at
            FROM agentguard_trace_steps WHERE id = $1
            """,
            step_id,
        )
        if row is None:
            return None
        d = dict(row)
        d["input"] = _decode_jsonb(d["input"])
        d["output"] = _decode_jsonb(d["output"])
        return d

    # -- LLM Gateway -----------------------------------------------------------

    async def list_llm_calls(self, model_name: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        if workspace_id is None:
            rows = await pool.fetch(
                """
                SELECT id, run_id, outcome, latency_ms, tokens_input, tokens_output, cost_usd, created_at
                FROM agentguard_trace_steps WHERE kind = 'llm_call' AND model_name = $1 ORDER BY created_at
                """,
                model_name,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT ts.id, ts.run_id, ts.outcome, ts.latency_ms, ts.tokens_input, ts.tokens_output,
                       ts.cost_usd, ts.created_at
                FROM agentguard_trace_steps ts
                JOIN agentguard_runs r ON r.id = ts.run_id
                WHERE ts.kind = 'llm_call' AND ts.model_name = $1 AND r.workspace_id = $2
                ORDER BY ts.created_at
                """,
                model_name, workspace_id,
            )
        return [dict(r) for r in rows]

    async def list_llm_models(self, workspace_id: str | None = None) -> list[str]:
        pool = await self._get_pool()
        if workspace_id is None:
            rows = await pool.fetch(
                "SELECT DISTINCT model_name FROM agentguard_trace_steps WHERE kind = 'llm_call' AND model_name IS NOT NULL ORDER BY model_name"
            )
        else:
            rows = await pool.fetch(
                """
                SELECT DISTINCT ts.model_name FROM agentguard_trace_steps ts
                JOIN agentguard_runs r ON r.id = ts.run_id
                WHERE ts.kind = 'llm_call' AND ts.model_name IS NOT NULL AND r.workspace_id = $1
                ORDER BY ts.model_name
                """,
                workspace_id,
            )
        return [r["model_name"] for r in rows]

    async def save_model_alternative(self, alternative: ModelAlternative) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_model_alternatives
                (id, workspace_id, primary_model, fallback_model, reliability_threshold, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (COALESCE(workspace_id, ''), primary_model) DO UPDATE SET
                fallback_model = EXCLUDED.fallback_model,
                reliability_threshold = EXCLUDED.reliability_threshold
            """,
            new_run_id(),
            alternative.workspace_id,
            alternative.primary_model,
            alternative.fallback_model,
            alternative.reliability_threshold,
            alternative.created_at,
        )

    async def list_model_alternatives(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT primary_model, fallback_model, reliability_threshold, created_at
            FROM agentguard_model_alternatives
            WHERE workspace_id IS NOT DISTINCT FROM $1
            """,
            workspace_id,
        )
        return [dict(r) for r in rows]

    async def save_tool_alternative(self, alternative: ToolAlternative) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_tool_alternatives (workspace_id, primary_tool, fallback_tool, reliability_threshold, created_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (workspace_id, primary_tool) DO UPDATE SET
                fallback_tool = EXCLUDED.fallback_tool,
                reliability_threshold = EXCLUDED.reliability_threshold
            """,
            alternative.workspace_id,
            alternative.primary,
            alternative.fallback,
            alternative.reliability_threshold,
            alternative.created_at,
        )

    async def list_tool_alternatives(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT primary_tool AS primary, fallback_tool AS fallback, reliability_threshold, created_at
            FROM agentguard_tool_alternatives
            WHERE workspace_id IS NOT DISTINCT FROM $1
            """,
            workspace_id,
        )
        return [dict(r) for r in rows]

    # -- Phase 4: behavior fingerprinting ------------------------------------

    async def list_runs_by_agent(
        self, agent_name: str, limit: int = 200, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id FROM agentguard_runs
            WHERE agent_name = $1 AND ($3::text IS NULL OR workspace_id = $3)
            ORDER BY started_at DESC LIMIT $2
            """,
            agent_name,
            limit,
            workspace_id,
        )
        result = []
        for r in rows:
            data = await self.get_run(r["id"])
            if data is not None:
                result.append(data)
        return result

    # -- Phase 4: policy control portal --------------------------------------

    async def save_policy_definition(self, definition: PolicyDefinition) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_policy_definitions (name, workspace_id, current_version, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (workspace_id, name) DO UPDATE SET
                current_version = EXCLUDED.current_version,
                updated_at = EXCLUDED.updated_at
            """,
            definition.name,
            definition.workspace_id,
            definition.current_version,
            definition.created_at,
            definition.updated_at,
        )

    async def get_policy_definition(self, name: str, workspace_id: str | None = None) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM agentguard_policy_definitions WHERE name = $1 AND workspace_id IS NOT DISTINCT FROM $2",
            name,
            workspace_id,
        )
        return dict(row) if row else None

    async def list_policy_definitions(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_policy_definitions WHERE workspace_id IS NOT DISTINCT FROM $1 ORDER BY name",
            workspace_id,
        )
        return [dict(r) for r in rows]

    async def save_policy_version(self, record: PolicyVersionRecord) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_policy_versions (id, policy_name, workspace_id, version, policy, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            record.id,
            record.policy_name,
            record.workspace_id,
            record.version,
            json.dumps(record.policy.model_dump(), default=str),
            record.created_at,
        )

    async def list_policy_versions(self, name: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT id, policy_name, version, policy, created_at FROM agentguard_policy_versions
            WHERE policy_name = $1 AND workspace_id IS NOT DISTINCT FROM $2 ORDER BY version
            """,
            name,
            workspace_id,
        )
        return [{**dict(r), "policy": _decode_jsonb(dict(r)["policy"])} for r in rows]

    async def get_policy_version(
        self, name: str, version: int, workspace_id: str | None = None
    ) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT id, policy_name, version, policy, created_at FROM agentguard_policy_versions
            WHERE policy_name = $1 AND version = $2 AND workspace_id IS NOT DISTINCT FROM $3
            """,
            name,
            version,
            workspace_id,
        )
        if row is None:
            return None
        d = dict(row)
        d["policy"] = _decode_jsonb(d["policy"])
        return d

    # -- Phase 4: auto-improvement --------------------------------------------

    async def save_improvement_candidate(self, candidate: ImprovementCandidate) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_improvement_candidates
                (id, source_run_id, workspace_id, problem, root_cause_summary, recommendation, candidate_change,
                 expected_benefit, optimizer, real_dspy_optimizer, status, approved_by, created_at, resolved_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                status = EXCLUDED.status, approved_by = EXCLUDED.approved_by, resolved_at = EXCLUDED.resolved_at
            """,
            candidate.id,
            candidate.source_run_id,
            candidate.workspace_id,
            candidate.problem,
            candidate.root_cause_summary,
            candidate.recommendation,
            json.dumps(candidate.candidate_change, default=str),
            candidate.expected_benefit,
            candidate.optimizer,
            candidate.real_dspy_optimizer,
            candidate.status,
            candidate.approved_by,
            candidate.created_at,
            candidate.resolved_at,
        )

    async def get_improvement_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_improvement_candidates WHERE id = $1", candidate_id)
        if row is None:
            return None
        d = dict(row)
        d["candidate_change"] = _decode_jsonb(d["candidate_change"])
        return d

    async def list_improvement_candidates(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_improvement_candidates WHERE workspace_id IS NOT DISTINCT FROM $1 ORDER BY created_at DESC",
            workspace_id,
        )
        return [{**dict(r), "candidate_change": _decode_jsonb(dict(r)["candidate_change"])} for r in rows]

    async def save_improvement_evaluation(self, evaluation: ImprovementEvaluation) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_improvement_evaluations
                (id, candidate_id, baseline_run_ids, candidate_run_ids, comparison, created_at)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6)
            """,
            evaluation.id,
            evaluation.candidate_id,
            json.dumps(evaluation.baseline_run_ids, default=str),
            json.dumps(evaluation.candidate_run_ids, default=str),
            json.dumps(evaluation.comparison, default=str),
            evaluation.created_at,
        )

    async def list_improvement_evaluations(self, candidate_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_improvement_evaluations WHERE candidate_id = $1 ORDER BY created_at",
            candidate_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            d["baseline_run_ids"] = _decode_jsonb(d["baseline_run_ids"])
            d["candidate_run_ids"] = _decode_jsonb(d["candidate_run_ids"])
            d["comparison"] = _decode_jsonb(d["comparison"])
            result.append(d)
        return result

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
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_problems
                (id, workspace_id, cluster_key, status, linked_candidate_id, triaged_by, triaged_at, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, now(), now(), now())
            ON CONFLICT (COALESCE(workspace_id, ''), cluster_key) DO UPDATE SET
                status = EXCLUDED.status,
                linked_candidate_id = EXCLUDED.linked_candidate_id,
                triaged_by = EXCLUDED.triaged_by,
                triaged_at = now(),
                updated_at = now()
            """,
            new_run_id(),
            workspace_id,
            cluster_key,
            status,
            linked_candidate_id,
            triaged_by,
        )

    async def get_problem_status(self, workspace_id: str | None, cluster_key: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM agentguard_problems WHERE COALESCE(workspace_id, '') = COALESCE($1, '') AND cluster_key = $2",
            workspace_id,
            cluster_key,
        )
        return dict(row) if row else None

    async def list_problem_statuses(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        if workspace_id is None:
            rows = await pool.fetch("SELECT * FROM agentguard_problems")
        else:
            rows = await pool.fetch("SELECT * FROM agentguard_problems WHERE workspace_id = $1", workspace_id)
        return [dict(r) for r in rows]

    # -- Phase 4: CI/CD reliability gate ---------------------------------------

    async def save_ci_gate_result(self, result: CIGateResult) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_ci_gate_results
                (id, baseline_run_ids, candidate_run_ids, threshold_pct, protected_metrics, regressions, result, created_at)
            VALUES ($1, $2::jsonb, $3::jsonb, $4, $5::jsonb, $6::jsonb, $7, $8)
            """,
            result.id,
            json.dumps(result.baseline_run_ids, default=str),
            json.dumps(result.candidate_run_ids, default=str),
            result.threshold_pct,
            json.dumps(result.protected_metrics, default=str),
            json.dumps(result.regressions, default=str),
            result.result,
            result.created_at,
        )

    async def list_ci_gate_results(self, limit: int = 50) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch("SELECT * FROM agentguard_ci_gate_results ORDER BY created_at DESC LIMIT $1", limit)
        result = []
        for r in rows:
            d = dict(r)
            d["baseline_run_ids"] = _decode_jsonb(d["baseline_run_ids"])
            d["candidate_run_ids"] = _decode_jsonb(d["candidate_run_ids"])
            d["protected_metrics"] = _decode_jsonb(d["protected_metrics"])
            d["regressions"] = _decode_jsonb(d["regressions"])
            result.append(d)
        return result

    # =========================================================================
    # Dashboard V2: multi-user authentication & tenancy
    # =========================================================================

    async def save_user(self, user: User) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_users (id, email, name, password_hash, is_active, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                password_hash = EXCLUDED.password_hash, is_active = EXCLUDED.is_active, name = EXCLUDED.name
            """,
            user.id,
            user.email.lower(),
            user.name,
            user.password_hash,
            user.is_active,
            user.created_at,
        )

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_users WHERE id = $1", user_id)
        return dict(row) if row else None

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_users WHERE email = $1", email.lower())
        return dict(row) if row else None

    async def save_session(self, session: Session) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_sessions (id, user_id, token_hash, created_at, expires_at, revoked_at, user_agent)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO UPDATE SET revoked_at = EXCLUDED.revoked_at
            """,
            session.id,
            session.user_id,
            session.token_hash,
            session.created_at,
            session.expires_at,
            session.revoked_at,
            session.user_agent,
        )

    async def get_session_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_sessions WHERE token_hash = $1", token_hash)
        return dict(row) if row else None

    async def revoke_session(self, session_id: str) -> None:
        pool = await self._get_pool()
        await pool.execute("UPDATE agentguard_sessions SET revoked_at = now() WHERE id = $1", session_id)

    async def list_sessions_for_user(self, user_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch("SELECT * FROM agentguard_sessions WHERE user_id = $1", user_id)
        return [dict(r) for r in rows]

    async def save_password_reset_token(self, token: PasswordResetToken) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_password_reset_tokens (id, user_id, token_hash, created_at, expires_at, used_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET used_at = EXCLUDED.used_at
            """,
            token.id,
            token.user_id,
            token.token_hash,
            token.created_at,
            token.expires_at,
            token.used_at,
        )

    async def get_password_reset_token_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_password_reset_tokens WHERE token_hash = $1", token_hash)
        return dict(row) if row else None

    async def mark_password_reset_token_used(self, token_id: str) -> None:
        pool = await self._get_pool()
        await pool.execute("UPDATE agentguard_password_reset_tokens SET used_at = now() WHERE id = $1", token_id)

    async def save_workspace(self, workspace: Workspace) -> None:
        pool = await self._get_pool()
        await pool.execute(
            "INSERT INTO agentguard_workspaces (id, name, created_at) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
            workspace.id,
            workspace.name,
            workspace.created_at,
        )

    async def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_workspaces WHERE id = $1", workspace_id)
        return dict(row) if row else None

    async def save_workspace_membership(self, membership: WorkspaceMembership) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_workspace_memberships (workspace_id, user_id, role, created_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (workspace_id, user_id) DO UPDATE SET role = EXCLUDED.role
            """,
            membership.workspace_id,
            membership.user_id,
            membership.role,
            membership.created_at,
        )

    async def get_membership(self, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM agentguard_workspace_memberships WHERE workspace_id = $1 AND user_id = $2",
            workspace_id,
            user_id,
        )
        return dict(row) if row else None

    async def list_memberships_for_user(self, user_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch("SELECT * FROM agentguard_workspace_memberships WHERE user_id = $1", user_id)
        return [dict(r) for r in rows]

    async def list_memberships_for_workspace(self, workspace_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_workspace_memberships WHERE workspace_id = $1", workspace_id
        )
        return [dict(r) for r in rows]

    async def save_project(self, project: Project) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_projects (id, workspace_id, name, created_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO NOTHING
            """,
            project.id,
            project.workspace_id,
            project.name,
            project.created_at,
        )

    async def get_project(self, project_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_projects WHERE id = $1", project_id)
        return dict(row) if row else None

    async def list_projects_for_workspace(self, workspace_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch("SELECT * FROM agentguard_projects WHERE workspace_id = $1", workspace_id)
        return [dict(r) for r in rows]

    async def save_agent_registration(self, agent: AgentRegistration) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_agents (id, workspace_id, project_id, name, created_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (workspace_id, name) DO NOTHING
            """,
            agent.id,
            agent.workspace_id,
            agent.project_id,
            agent.name,
            agent.created_at,
        )

    async def get_agent_registration(self, workspace_id: str, name: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM agentguard_agents WHERE workspace_id = $1 AND name = $2", workspace_id, name
        )
        if row is None:
            return None
        d = dict(row)
        d["capabilities"] = _decode_jsonb(d["capabilities"]) if d.get("capabilities") is not None else []
        return d

    async def list_agent_registrations(self, workspace_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch("SELECT * FROM agentguard_agents WHERE workspace_id = $1", workspace_id)
        result = []
        for r in rows:
            d = dict(r)
            d["capabilities"] = _decode_jsonb(d["capabilities"]) if d.get("capabilities") is not None else []
            result.append(d)
        return result

    async def update_agent_card(
        self,
        workspace_id: str,
        name: str,
        *,
        description: str | None = None,
        capabilities: list[str] | None = None,
    ) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            UPDATE agentguard_agents
            SET description = COALESCE($3, description),
                capabilities = COALESCE($4::jsonb, capabilities)
            WHERE workspace_id = $1 AND name = $2
            """,
            workspace_id,
            name,
            description,
            json.dumps(capabilities, default=str) if capabilities is not None else None,
        )

    async def save_api_key(self, api_key: ApiKey) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_api_keys
                (id, user_id, workspace_id, project_id, name, key_prefix, key_hash, created_at, last_used_at, revoked_at, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, status = EXCLUDED.status, revoked_at = EXCLUDED.revoked_at,
                last_used_at = EXCLUDED.last_used_at
            """,
            api_key.id,
            api_key.user_id,
            api_key.workspace_id,
            api_key.project_id,
            api_key.name,
            api_key.key_prefix,
            api_key.key_hash,
            api_key.created_at,
            api_key.last_used_at,
            api_key.revoked_at,
            api_key.status,
        )

    async def get_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_api_keys WHERE key_hash = $1", key_hash)
        return dict(row) if row else None

    async def get_api_key_by_id(self, key_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_api_keys WHERE id = $1", key_id)
        return dict(row) if row else None

    async def list_api_keys_for_user(self, user_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch("SELECT * FROM agentguard_api_keys WHERE user_id = $1 ORDER BY created_at DESC", user_id)
        return [dict(r) for r in rows]

    async def update_api_key_last_used(self, key_id: str) -> None:
        pool = await self._get_pool()
        await pool.execute("UPDATE agentguard_api_keys SET last_used_at = now() WHERE id = $1", key_id)

    # -- Evaluation Platform ---------------------------------------------------

    async def save_evaluation_suite(self, suite: EvaluationSuite) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_evaluation_suites (id, workspace_id, name, app_type, metrics, version, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, app_type = EXCLUDED.app_type,
                metrics = EXCLUDED.metrics, version = EXCLUDED.version
            """,
            suite.id,
            suite.workspace_id,
            suite.name,
            suite.app_type,
            json.dumps([m.model_dump() for m in suite.metrics], default=str),
            suite.version,
            suite.created_at,
        )

    async def get_evaluation_suite(self, suite_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_evaluation_suites WHERE id = $1", suite_id)
        if row is None:
            return None
        d = dict(row)
        d["metrics"] = _decode_jsonb(d["metrics"])
        return d

    async def list_evaluation_suites(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_evaluation_suites WHERE workspace_id IS NOT DISTINCT FROM $1 ORDER BY created_at DESC",
            workspace_id,
        )
        return [{**dict(r), "metrics": _decode_jsonb(dict(r)["metrics"])} for r in rows]

    async def save_evaluation_run(self, evaluation_run: EvaluationRun) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_evaluation_runs
                (id, suite_id, workspace_id, source_run_ids, status, started_at, finished_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            ON CONFLICT (id) DO UPDATE SET
                source_run_ids = EXCLUDED.source_run_ids, status = EXCLUDED.status,
                finished_at = EXCLUDED.finished_at
            """,
            evaluation_run.id,
            evaluation_run.suite_id,
            evaluation_run.workspace_id,
            json.dumps(evaluation_run.source_run_ids, default=str),
            evaluation_run.status,
            evaluation_run.started_at,
            evaluation_run.finished_at,
        )

    async def get_evaluation_run(self, evaluation_run_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_evaluation_runs WHERE id = $1", evaluation_run_id)
        if row is None:
            return None
        d = dict(row)
        d["source_run_ids"] = _decode_jsonb(d["source_run_ids"])
        return d

    async def list_evaluation_runs(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_evaluation_runs WHERE workspace_id IS NOT DISTINCT FROM $1 ORDER BY started_at DESC",
            workspace_id,
        )
        return [{**dict(r), "source_run_ids": _decode_jsonb(dict(r)["source_run_ids"])} for r in rows]

    async def save_evaluation_result(self, result: EvaluationResult) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_evaluation_results
                (id, evaluation_run_id, source_run_id, source_step_id, metric, score, passed, available, reason,
                 confidence, judge_model, cost_usd, latency_ms, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """,
            result.id,
            result.evaluation_run_id,
            result.source_run_id,
            result.source_step_id,
            result.metric,
            result.score,
            result.passed,
            result.available,
            result.reason,
            result.confidence,
            result.judge_model,
            result.cost_usd,
            result.latency_ms,
            result.created_at,
        )

    async def list_evaluation_results(self, evaluation_run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_evaluation_results WHERE evaluation_run_id = $1 ORDER BY created_at",
            evaluation_run_id,
        )
        return [dict(r) for r in rows]

    async def get_evaluation_result(self, result_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_evaluation_results WHERE id = $1", result_id)
        return dict(row) if row else None

    # -- Evaluation Platform — Phase 3: Golden Dataset Validation ---------------

    async def save_dataset(self, dataset: Dataset) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_datasets (id, workspace_id, name, current_version, created_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, current_version = EXCLUDED.current_version
            """,
            dataset.id, dataset.workspace_id, dataset.name, dataset.current_version, dataset.created_at,
        )

    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_datasets WHERE id = $1", dataset_id)
        return dict(row) if row else None

    async def list_datasets(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_datasets WHERE workspace_id IS NOT DISTINCT FROM $1 ORDER BY created_at DESC",
            workspace_id,
        )
        return [dict(r) for r in rows]

    async def save_dataset_version(self, version: DatasetVersion) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_dataset_versions (id, dataset_id, version, created_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO NOTHING
            """,
            version.id, version.dataset_id, version.version, version.created_at,
        )

    async def list_dataset_versions(self, dataset_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_dataset_versions WHERE dataset_id = $1 ORDER BY version", dataset_id
        )
        return [dict(r) for r in rows]

    async def get_dataset_version(self, dataset_id: str, version: int) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM agentguard_dataset_versions WHERE dataset_id = $1 AND version = $2", dataset_id, version
        )
        return dict(row) if row else None

    async def save_dataset_example(self, example: DatasetExample) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_dataset_examples
                (id, dataset_version_id, question, expected_answer, golden_status,
                 validation_confidence, validation_reason, validated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                golden_status = EXCLUDED.golden_status, validation_confidence = EXCLUDED.validation_confidence,
                validation_reason = EXCLUDED.validation_reason, validated_at = EXCLUDED.validated_at
            """,
            example.id, example.dataset_version_id, example.question, example.expected_answer,
            example.golden_status, example.validation_confidence, example.validation_reason, example.validated_at,
        )

    async def get_dataset_example(self, example_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_dataset_examples WHERE id = $1", example_id)
        return dict(row) if row else None

    async def list_dataset_examples(self, dataset_version_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_dataset_examples WHERE dataset_version_id = $1", dataset_version_id
        )
        return [dict(r) for r in rows]

    async def save_evidence(self, evidence: Evidence) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_evidence
                (id, example_id, source_ref, text, retrieval_score, retrieved_at, cited_in_verdict)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            evidence.id, evidence.example_id, evidence.source_ref, evidence.text,
            evidence.retrieval_score, evidence.retrieved_at, evidence.cited_in_verdict,
        )

    async def list_evidence_for_example(self, example_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_evidence WHERE example_id = $1 ORDER BY retrieval_score DESC", example_id
        )
        return [dict(r) for r in rows]

    # -- Evaluation Platform — Phase 7/8: recommendation + benchmarking --------

    async def save_recommendation(self, recommendation: Recommendation) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_recommendations
                (id, workspace_id, kind, subject_id, recommendation, reasoning, evidence_ids, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8)
            """,
            recommendation.id, recommendation.workspace_id, recommendation.kind, recommendation.subject_id,
            json.dumps(recommendation.recommendation, default=str), recommendation.reasoning,
            json.dumps(recommendation.evidence_ids, default=str), recommendation.created_at,
        )

    async def list_recommendations(
        self, kind: str | None = None, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT * FROM agentguard_recommendations
            WHERE (kind = $1 OR $1 IS NULL) AND workspace_id IS NOT DISTINCT FROM $2
            ORDER BY created_at DESC
            """,
            kind, workspace_id,
        )
        return [{**dict(r), "recommendation": _decode_jsonb(dict(r)["recommendation"]),
                 "evidence_ids": _decode_jsonb(dict(r)["evidence_ids"])} for r in rows]

    async def save_model_benchmark(self, benchmark: ModelBenchmark) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_model_benchmarks
                (id, workspace_id, dataset_version_id, suite_id, models, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            benchmark.id, benchmark.workspace_id, benchmark.dataset_version_id, benchmark.suite_id,
            json.dumps(benchmark.models, default=str), benchmark.created_at,
        )

    async def get_model_benchmark(self, benchmark_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_model_benchmarks WHERE id = $1", benchmark_id)
        if row is None:
            return None
        d = dict(row)
        d["models"] = _decode_jsonb(d["models"])
        return d

    async def list_model_benchmarks(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_model_benchmarks WHERE workspace_id IS NOT DISTINCT FROM $1 ORDER BY created_at DESC",
            workspace_id,
        )
        return [{**dict(r), "models": _decode_jsonb(dict(r)["models"])} for r in rows]

    async def save_model_benchmark_result(self, result: ModelBenchmarkResult) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_model_benchmark_results
                (id, benchmark_id, model, example_id, evaluation_result_id, cost_usd, latency_ms, tokens_input, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            result.id, result.benchmark_id, result.model, result.example_id, result.evaluation_result_id,
            result.cost_usd, result.latency_ms, result.tokens_input, result.created_at,
        )

    async def list_model_benchmark_results(self, benchmark_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_model_benchmark_results WHERE benchmark_id = $1 ORDER BY created_at",
            benchmark_id,
        )
        return [dict(r) for r in rows]

    # -- Evaluation Platform — Phase 9: evaluator-of-evaluators -----------------

    async def save_judge_calibration_example(self, example: JudgeCalibrationExample) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_judge_calibration_examples
                (id, metric, workspace_id, case_input, case_actual_output, human_label, created_at)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7)
            """,
            example.id, example.metric, example.workspace_id,
            json.dumps(example.case_input, default=str), json.dumps(example.case_actual_output, default=str),
            json.dumps(example.human_label, default=str), example.created_at,
        )

    async def list_judge_calibration_examples(
        self, metric: str, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT * FROM agentguard_judge_calibration_examples WHERE metric = $1 AND workspace_id IS NOT DISTINCT FROM $2",
            metric, workspace_id,
        )
        return [{**dict(r), "case_input": _decode_jsonb(dict(r)["case_input"]),
                 "case_actual_output": _decode_jsonb(dict(r)["case_actual_output"]),
                 "human_label": _decode_jsonb(dict(r)["human_label"])} for r in rows]

    # -- Evaluation Platform — Phase 5: async job queue -------------------------

    async def enqueue_job(self, job: Job) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO agentguard_jobs
                (id, kind, payload, status, attempts, max_attempts, error, workspace_id, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8, $9, $10)
            """,
            job.id, job.kind, json.dumps(job.payload, default=str), job.status, job.attempts,
            job.max_attempts, job.error, job.workspace_id, job.created_at, job.updated_at,
        )

    async def claim_next_job(self, kinds: list[str] | None = None) -> dict[str, Any] | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE agentguard_jobs SET status = 'running', updated_at = now()
                    WHERE id = (
                        SELECT id FROM agentguard_jobs
                        WHERE status = 'pending' AND (kind = ANY($1) OR $1 IS NULL)
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    kinds,
                )
        if row is None:
            return None
        d = dict(row)
        d["payload"] = _decode_jsonb(d["payload"])
        return d

    async def complete_job(self, job_id: str) -> None:
        pool = await self._get_pool()
        await pool.execute("UPDATE agentguard_jobs SET status = 'complete', updated_at = now() WHERE id = $1", job_id)

    async def fail_job(self, job_id: str, error: str, *, retry: bool) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            UPDATE agentguard_jobs SET
                attempts = attempts + 1,
                error = $2,
                updated_at = now(),
                status = CASE WHEN $3 AND attempts + 1 < max_attempts THEN 'pending' ELSE 'failed' END
            WHERE id = $1
            """,
            job_id, error, retry,
        )

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM agentguard_jobs WHERE id = $1", job_id)
        if row is None:
            return None
        d = dict(row)
        d["payload"] = _decode_jsonb(d["payload"])
        return d

    async def list_jobs(self, status: str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT * FROM agentguard_jobs
            WHERE (status = $1 OR $1 IS NULL) AND workspace_id IS NOT DISTINCT FROM $2
            ORDER BY created_at DESC
            """,
            status, workspace_id,
        )
        return [{**dict(r), "payload": _decode_jsonb(dict(r)["payload"])} for r in rows]
