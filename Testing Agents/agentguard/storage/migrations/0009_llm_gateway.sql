-- LLM Gateway migration (agentguard/tracing/litellm_wrap.py,
-- agentguard/reliability/model_profile.py) — a model_name column on
-- TraceStep (so kind="llm_call" rows can be grouped/profiled per
-- model, mirroring how agentguard_tool_calls.tool already works) and
-- an explicit primary -> fallback model registration table.
--
-- Purely additive: ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT
-- EXISTS only. agentguard_model_alternatives uses a synthetic id
-- PRIMARY KEY + a COALESCE-based unique index rather than a composite
-- PRIMARY KEY (workspace_id, primary_model) — Postgres forces every
-- PRIMARY KEY column NOT NULL, which is exactly the pre-existing
-- mistake in agentguard_tool_alternatives (0005_dashboard_v2.sql's
-- "PRIMARY KEY (workspace_id, primary_tool)" silently forces
-- workspace_id NOT NULL there, contradicting that migration's own
-- stated intent that pre-Dashboard-V2 rows keep workspace_id NULL).

ALTER TABLE agentguard_trace_steps ADD COLUMN IF NOT EXISTS model_name TEXT;
CREATE INDEX IF NOT EXISTS idx_agentguard_trace_steps_model_name
    ON agentguard_trace_steps (model_name) WHERE kind = 'llm_call';

CREATE TABLE IF NOT EXISTS agentguard_model_alternatives (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    primary_model TEXT NOT NULL,
    fallback_model TEXT NOT NULL,
    reliability_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agentguard_model_alternatives_ws_primary
    ON agentguard_model_alternatives (COALESCE(workspace_id, ''), primary_model);
