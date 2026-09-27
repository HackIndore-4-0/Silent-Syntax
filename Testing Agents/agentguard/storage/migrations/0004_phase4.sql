-- AgentGuard Phase 4 migration — Replay, CLI & Auto-Improve.
--
-- Purely additive: every statement is IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS, so re-running it (or running it against a database that
-- already has Phase 1-3 data) never destroys anything.

-- agentguard_runs: agent version labeling, for Regression Comparison.
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS agent_version TEXT;

-- agentguard_policies: what happens when call_tool()'s primary AND its
-- registered alternative are exhausted.
ALTER TABLE agentguard_policies ADD COLUMN IF NOT EXISTS on_tool_exhausted TEXT NOT NULL DEFAULT 'replan';

-- Raw per-call tool evidence — ToolProfile is computed FROM this table,
-- never stored redundantly as its own row.
CREATE TABLE IF NOT EXISTS agentguard_tool_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    tool TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    outcome TEXT NOT NULL,
    duplicate BOOLEAN NOT NULL DEFAULT false,
    is_fallback BOOLEAN NOT NULL DEFAULT false,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Explicitly registered primary -> fallback tool mappings (Rule: no
-- arbitrary tool substitution). One row per primary tool.
CREATE TABLE IF NOT EXISTS agentguard_tool_alternatives (
    primary_tool TEXT PRIMARY KEY,
    fallback_tool TEXT NOT NULL,
    reliability_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Named, versioned policies managed through the Control Portal. Distinct
-- from agentguard_policies (which is the immutable, per-run snapshot
-- every run already carries and which this table never touches).
CREATE TABLE IF NOT EXISTS agentguard_policy_definitions (
    name TEXT PRIMARY KEY,
    current_version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_policy_versions (
    id TEXT PRIMARY KEY,
    policy_name TEXT NOT NULL REFERENCES agentguard_policy_definitions(name) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    policy JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (policy_name, version)
);

CREATE TABLE IF NOT EXISTS agentguard_improvement_candidates (
    id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    problem TEXT NOT NULL,
    root_cause_summary TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    candidate_change JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected_benefit TEXT NOT NULL DEFAULT '',
    optimizer TEXT NOT NULL DEFAULT '',
    real_dspy_optimizer BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'proposed',
    approved_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agentguard_improvement_evaluations (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES agentguard_improvement_candidates(id) ON DELETE CASCADE,
    baseline_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    candidate_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    comparison JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_ci_gate_results (
    id TEXT PRIMARY KEY,
    baseline_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    candidate_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    threshold_pct DOUBLE PRECISION NOT NULL,
    protected_metrics JSONB NOT NULL DEFAULT '[]'::jsonb,
    regressions JSONB NOT NULL DEFAULT '{}'::jsonb,
    result TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agentguard_tool_calls_run_id ON agentguard_tool_calls (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_tool_calls_tool ON agentguard_tool_calls (tool);
CREATE INDEX IF NOT EXISTS idx_agentguard_runs_agent_name_started_at ON agentguard_runs (agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agentguard_policy_versions_name ON agentguard_policy_versions (policy_name);
CREATE INDEX IF NOT EXISTS idx_agentguard_improvement_evaluations_candidate ON agentguard_improvement_evaluations (candidate_id);
