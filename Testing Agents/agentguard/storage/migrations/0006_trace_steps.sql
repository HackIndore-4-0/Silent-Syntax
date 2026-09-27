-- AgentGuard TraceStep tracing migration — fine-grained sub-run tracing
-- for @traceable / wrap_llm_client (see agentguard/tracing/).
--
-- Purely additive: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
-- EXISTS only, no ALTER/DROP of any existing constraint. Mirrors
-- agentguard_tool_calls's conventions (migrations/0004_phase4.sql),
-- plus a nullable parent_step_id following agentguard_spans.parent_span_id's
-- precedent (no self-referential FK constraint, by design).

CREATE TABLE IF NOT EXISTS agentguard_trace_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    parent_step_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    output JSONB,
    outcome TEXT NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    exception_type TEXT,
    exception_message TEXT,
    traceback_text TEXT,
    code_file TEXT,
    code_function TEXT,
    code_lineno INTEGER,
    tokens_input INTEGER,
    tokens_output INTEGER,
    cost_usd DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agentguard_trace_steps_run_id ON agentguard_trace_steps (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_trace_steps_parent_step_id ON agentguard_trace_steps (parent_step_id);
