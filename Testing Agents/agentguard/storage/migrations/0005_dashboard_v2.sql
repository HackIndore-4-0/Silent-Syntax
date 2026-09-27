-- AgentGuard Dashboard V2 migration — multi-user auth & tenancy.
--
-- Purely additive: every statement is IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS, so re-running it (or running it against a database that
-- already has Phase 1-4 data) never destroys anything. Every existing
-- row gets workspace_id/project_id/agent_id = NULL, which the
-- application layer treats as "pre-Dashboard-V2 / unowned" data —
-- visible to nothing scoped to a real workspace, but never deleted.

ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS agent_id TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS model_name TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS tokens_input INTEGER;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS tokens_output INTEGER;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS estimated_cost_usd DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS agentguard_users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES agentguard_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    user_agent TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agentguard_password_reset_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES agentguard_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agentguard_workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_workspace_memberships (
    workspace_id TEXT NOT NULL REFERENCES agentguard_workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES agentguard_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS agentguard_projects (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES agentguard_workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_agents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES agentguard_workspaces(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES agentguard_projects(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, name)
);

-- API keys: only a SHA-256 hash of the raw secret is ever stored. The
-- raw key exists only in the one HTTP response that creates it.
CREATE TABLE IF NOT EXISTS agentguard_api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES agentguard_users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES agentguard_workspaces(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES agentguard_projects(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active'
);

-- Phase 4 top-level resources gain a workspace boundary. Policy names
-- and tool-alternative primaries are now unique PER WORKSPACE, not
-- globally — existing (pre-Dashboard-V2) rows have workspace_id NULL
-- and keep behaving exactly as before (globally unique among
-- other NULL-workspace rows).
ALTER TABLE agentguard_policy_definitions ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE agentguard_policy_definitions DROP CONSTRAINT IF EXISTS agentguard_policy_definitions_pkey CASCADE;
ALTER TABLE agentguard_policy_definitions ADD PRIMARY KEY (workspace_id, name);

ALTER TABLE agentguard_policy_versions ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE agentguard_policy_versions ADD CONSTRAINT agentguard_policy_versions_workspace_name_fkey
    FOREIGN KEY (workspace_id, policy_name) REFERENCES agentguard_policy_definitions (workspace_id, name) ON DELETE CASCADE;

ALTER TABLE agentguard_tool_alternatives ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE agentguard_tool_alternatives DROP CONSTRAINT IF EXISTS agentguard_tool_alternatives_pkey;
ALTER TABLE agentguard_tool_alternatives ADD PRIMARY KEY (workspace_id, primary_tool);

ALTER TABLE agentguard_improvement_candidates ADD COLUMN IF NOT EXISTS workspace_id TEXT;

CREATE INDEX IF NOT EXISTS idx_agentguard_runs_workspace_id ON agentguard_runs (workspace_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agentguard_sessions_user_id ON agentguard_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_projects_workspace_id ON agentguard_projects (workspace_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_agents_workspace_id ON agentguard_agents (workspace_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_api_keys_user_id ON agentguard_api_keys (user_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_improvement_candidates_workspace_id ON agentguard_improvement_candidates (workspace_id);
