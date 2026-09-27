-- AgentGuard Cross-run Improvement Engine migration — persisted human
-- triage state for clustered failures (agentguard/reliability/cluster.py).
--
-- Purely additive: CREATE TABLE IF NOT EXISTS only, no ALTER/DROP. Cluster
-- MEMBERSHIP (which runs belong, count, confidence, priority) is never
-- stored here — it is recomputed fresh from agentguard_runs /
-- agentguard_root_causes / agentguard_trace_steps on every request (same
-- "recompute, don't cache" rule as agentguard_tool_calls -> ToolProfile).
-- This table stores ONLY the one thing that has nowhere else to live: a
-- human's triage decision on a problem, keyed by its stable cluster_key,
-- which must survive being recomputed.
--
-- workspace_id is nullable (pre-Dashboard-V2 / unowned data uses NULL), so
-- it is deliberately NOT part of a composite PRIMARY KEY: Postgres forces
-- every PRIMARY KEY column to be NOT NULL, which is exactly the mistake
-- already present in agentguard_tool_alternatives (0005_dashboard_v2.sql
-- added "PRIMARY KEY (workspace_id, primary_tool)", silently forcing
-- workspace_id NOT NULL there, contradicting that same migration's own
-- stated intent). This table uses a synthetic id PRIMARY KEY plus a
-- COALESCE-based unique index instead, which does accept NULL.

CREATE TABLE IF NOT EXISTS agentguard_problems (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    cluster_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    linked_candidate_id TEXT REFERENCES agentguard_improvement_candidates(id) ON DELETE SET NULL,
    triaged_by TEXT,
    triaged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agentguard_problems_ws_cluster
    ON agentguard_problems (COALESCE(workspace_id, ''), cluster_key);
CREATE INDEX IF NOT EXISTS idx_agentguard_problems_workspace_id ON agentguard_problems (workspace_id);
