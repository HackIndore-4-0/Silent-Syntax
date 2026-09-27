-- Async job queue (Phase 5, agentguard/jobs/) — a durable alternative
-- to the in-process asyncio.create_task background trace-write
-- (agentguard/tracing/_pending.py) for work that can run for minutes
-- to hours and must survive a process restart (a large dataset
-- validation, a model benchmark). Purely additive.

CREATE TABLE IF NOT EXISTS agentguard_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    error TEXT,
    workspace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_jobs_status_created ON agentguard_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_agentguard_jobs_workspace_id ON agentguard_jobs (workspace_id);
