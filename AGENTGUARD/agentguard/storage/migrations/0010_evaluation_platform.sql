-- Evaluation Platform (agentguard/evaluation/) — Phase 1: core storage
-- for EvaluationSuite / EvaluationRun / EvaluationResult. Purely
-- additive (CREATE TABLE IF NOT EXISTS only). Every table uses a
-- synthetic `id TEXT PRIMARY KEY` — none of these need a composite key
-- over a nullable workspace_id, so there is no COALESCE-unique-index
-- gotcha to repeat here (see 0009_llm_gateway.sql's comment for why
-- that pattern matters elsewhere).

CREATE TABLE IF NOT EXISTS agentguard_evaluation_suites (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    name TEXT NOT NULL,
    app_type TEXT,
    metrics JSONB NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_evaluation_suites_workspace_id
    ON agentguard_evaluation_suites (workspace_id);

CREATE TABLE IF NOT EXISTS agentguard_evaluation_runs (
    id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL REFERENCES agentguard_evaluation_suites(id) ON DELETE CASCADE,
    workspace_id TEXT,
    source_run_ids JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agentguard_evaluation_runs_workspace_id
    ON agentguard_evaluation_runs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_evaluation_runs_suite_id
    ON agentguard_evaluation_runs (suite_id);

CREATE TABLE IF NOT EXISTS agentguard_evaluation_results (
    id TEXT PRIMARY KEY,
    evaluation_run_id TEXT NOT NULL REFERENCES agentguard_evaluation_runs(id) ON DELETE CASCADE,
    source_run_id TEXT,
    metric TEXT NOT NULL,
    score DOUBLE PRECISION,
    passed BOOLEAN,
    available BOOLEAN NOT NULL DEFAULT true,
    reason TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION,
    judge_model TEXT,
    cost_usd DOUBLE PRECISION,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_evaluation_results_run_id
    ON agentguard_evaluation_results (evaluation_run_id);
