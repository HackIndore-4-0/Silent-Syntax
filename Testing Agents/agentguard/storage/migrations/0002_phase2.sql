-- AgentGuard Phase 2 migration — Reliability & Risk.
--
-- Purely additive: every statement is IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS, so re-running it (or running it against a database that
-- already has Phase 1 data) never destroys anything.

-- agentguard_runs: retry/replan counters, surfaced on the run itself so
-- the dashboard's run list doesn't need to join decisions for them.
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS replan_count INTEGER NOT NULL DEFAULT 0;

-- agentguard_policies: the new declarative Policy fields.
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS require_approval JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS forbidden_actions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS default_on_uncertain TEXT NOT NULL DEFAULT 'stop';
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS retry_limit INTEGER NOT NULL DEFAULT 2;
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS on_retry_exhausted TEXT NOT NULL DEFAULT 'replan';
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS max_replans INTEGER NOT NULL DEFAULT 1;
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS human_timeout_s DOUBLE PRECISION NOT NULL DEFAULT 120;

-- agentguard_evaluations: confidence/reason (EvalResult Phase 2 fields).
ALTER TABLE agentguard_evaluations ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0;
ALTER TABLE agentguard_evaluations ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT '';

-- agentguard_decisions: RETRY/REPLAN/HUMAN are new valid `outcome`
-- values (outcome stays TEXT, no CHECK constraint change needed), plus
-- the structured evidence fields every decision now carries.
ALTER TABLE agentguard_decisions ADD COLUMN IF NOT EXISTS risk_score DOUBLE PRECISION;
ALTER TABLE agentguard_decisions ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION;
ALTER TABLE agentguard_decisions ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agentguard_decisions ADD COLUMN IF NOT EXISTS replan_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS agentguard_risk_assessments (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    action TEXT,
    risk_score DOUBLE PRECISION NOT NULL,
    impact DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    factors JSONB NOT NULL DEFAULT '{}'::jsonb,
    weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    explanation TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_root_causes (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    earliest_deviation TEXT NOT NULL,
    expected JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL,
    explanation TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_human_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    action TEXT,
    decision TEXT NOT NULL,
    risk_score DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    reason TEXT NOT NULL DEFAULT '',
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    timeout_s DOUBLE PRECISION NOT NULL DEFAULT 120,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_agentguard_risk_assessments_run_id
    ON agentguard_risk_assessments (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_root_causes_run_id
    ON agentguard_root_causes (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_human_decisions_run_id
    ON agentguard_human_decisions (run_id);
