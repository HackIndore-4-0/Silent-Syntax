-- AgentGuard Phase 1 schema.
--
-- Applied automatically by PostgresRunRepository.init_schema(), or run
-- by hand with: psql "$AGENTGUARD_DATABASE_URL" -f schema.sql
--
-- `id` / `run_id` columns are TEXT, not UUID: agentguard.models.Run
-- already generates a str UUID in Python (uuid.uuid4()), so storing it
-- as TEXT avoids an extra type-cast layer between asyncpg and Postgres
-- for no benefit at Phase 1's scale.

CREATE TABLE IF NOT EXISTS agentguard_runs (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    task TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    initial_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_state JSONB,
    exception_type TEXT,
    exception_message TEXT,
    trace_id TEXT,
    span_id TEXT
);

CREATE TABLE IF NOT EXISTS agentguard_policies (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    max_cost DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_spans (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS agentguard_evaluations (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    evaluator TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    label TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_decisions (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agentguard_runs_started_at
    ON agentguard_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agentguard_policies_run_id
    ON agentguard_policies (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_spans_run_id
    ON agentguard_spans (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_evaluations_run_id
    ON agentguard_evaluations (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_decisions_run_id
    ON agentguard_decisions (run_id);
