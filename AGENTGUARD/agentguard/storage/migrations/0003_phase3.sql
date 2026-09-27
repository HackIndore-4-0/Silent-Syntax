-- AgentGuard Phase 3 migration — Demo Polish & Recovery.
--
-- Purely additive: every statement is IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS, so re-running it (or running it against a database that
-- already has Phase 1/2 data) never destroys anything.

-- agentguard_runs: Tool Usage evidence, and recovery-run lineage.
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS actions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT REFERENCES agentguard_runs(id) ON DELETE SET NULL;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS recovery_checkpoint_id TEXT;

-- agentguard_policies: declarative policy versioning, so every
-- audit-relevant event can reference "what policy was active" without
-- ever retroactively changing a completed run's recorded version.
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS agentguard_checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    seq INTEGER NOT NULL,
    state_hash TEXT NOT NULL,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_audit_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    policy_version INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

CREATE TABLE IF NOT EXISTS agentguard_counterfactuals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    source_checkpoint_id TEXT NOT NULL,
    actual_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    counterfactual_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    altered_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    result TEXT NOT NULL DEFAULT '',
    comparison JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agentguard_checkpoints_run_id
    ON agentguard_checkpoints (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_audit_events_run_id
    ON agentguard_audit_events (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_counterfactuals_run_id
    ON agentguard_counterfactuals (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_runs_parent_run_id
    ON agentguard_runs (parent_run_id);
