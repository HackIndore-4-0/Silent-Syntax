-- AgentGuard full schema — the end state produced by applying
-- migrations/0001_phase1.sql, then 0002_phase2.sql, then
-- 0003_phase3.sql, then 0004_phase4.sql, in order.
--
-- This file is kept as a single-file reference of "what the schema
-- looks like today"; it is NOT what PostgresRunRepository.init_schema()
-- runs. init_schema() applies agentguard/storage/migrations/*.sql in
-- filename order (tracked in `agentguard_schema_migrations`), which is
-- the actual migration mechanism (Rule: "use migrations").
--
-- `id` / `run_id` columns are TEXT, not UUID: agentguard.models.Run
-- already generates a str UUID in Python (uuid.uuid4()), so storing it
-- as TEXT avoids an extra type-cast layer between asyncpg and Postgres
-- for no benefit at this scale.

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
    span_id TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    replan_count INTEGER NOT NULL DEFAULT 0,
    actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    parent_run_id TEXT REFERENCES agentguard_runs(id) ON DELETE SET NULL,
    recovery_checkpoint_id TEXT,
    agent_version TEXT
);

CREATE TABLE IF NOT EXISTS agentguard_policies (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    max_cost DOUBLE PRECISION,
    require_approval JSONB NOT NULL DEFAULT '[]'::jsonb,
    forbidden_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    default_on_uncertain TEXT NOT NULL DEFAULT 'stop',
    retry_limit INTEGER NOT NULL DEFAULT 2,
    on_retry_exhausted TEXT NOT NULL DEFAULT 'replan',
    max_replans INTEGER NOT NULL DEFAULT 1,
    human_timeout_s DOUBLE PRECISION NOT NULL DEFAULT 120,
    version INTEGER NOT NULL DEFAULT 1,
    on_tool_exhausted TEXT NOT NULL DEFAULT 'replan',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 4 --------------------------------------------------------------

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

CREATE TABLE IF NOT EXISTS agentguard_tool_alternatives (
    primary_tool TEXT PRIMARY KEY,
    fallback_tool TEXT NOT NULL,
    reliability_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    reason TEXT NOT NULL DEFAULT '',
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentguard_decisions (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agentguard_runs(id) ON DELETE CASCADE,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_score DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    retry_count INTEGER NOT NULL DEFAULT 0,
    replan_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    resolved_by TEXT,
    modified_evidence JSONB
);

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
CREATE INDEX IF NOT EXISTS idx_agentguard_risk_assessments_run_id
    ON agentguard_risk_assessments (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_root_causes_run_id
    ON agentguard_root_causes (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_human_decisions_run_id
    ON agentguard_human_decisions (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_checkpoints_run_id
    ON agentguard_checkpoints (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_audit_events_run_id
    ON agentguard_audit_events (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_counterfactuals_run_id
    ON agentguard_counterfactuals (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_runs_parent_run_id
    ON agentguard_runs (parent_run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_tool_calls_run_id
    ON agentguard_tool_calls (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_tool_calls_tool
    ON agentguard_tool_calls (tool);
CREATE INDEX IF NOT EXISTS idx_agentguard_runs_agent_name_started_at
    ON agentguard_runs (agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agentguard_policy_versions_name
    ON agentguard_policy_versions (policy_name);
CREATE INDEX IF NOT EXISTS idx_agentguard_improvement_evaluations_candidate
    ON agentguard_improvement_evaluations (candidate_id);

-- NOTE: this file was already missing migration 0005_dashboard_v2.sql's
-- tables (agentguard_users, agentguard_sessions, agentguard_workspaces,
-- etc.) before the block below was added — pre-existing drift, not
-- introduced or fixed here. Treat this file as informational only;
-- PostgresRunRepository.init_schema() applying migrations/*.sql in
-- filename order is the real, authoritative mechanism.

-- TraceStep tracing (0006_trace_steps.sql) -------------------------------
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

CREATE INDEX IF NOT EXISTS idx_agentguard_trace_steps_run_id
    ON agentguard_trace_steps (run_id);
CREATE INDEX IF NOT EXISTS idx_agentguard_trace_steps_parent_step_id
    ON agentguard_trace_steps (parent_step_id);

-- Cross-run failure clustering (0007_failure_clusters.sql) ----------------
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

-- A2A Agent Card (0008_a2a_card.sql) --------------------------------------
ALTER TABLE agentguard_agents ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE agentguard_agents ADD COLUMN IF NOT EXISTS capabilities JSONB NOT NULL DEFAULT '[]'::jsonb;

-- LLM Gateway (0009_llm_gateway.sql) ---------------------------------------
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

-- Evaluation Platform (0010_evaluation_platform.sql) -----------------------
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

-- Evaluation Platform phases 3/7/8/9 (0011_eval_platform_phase3789.sql) -----
CREATE TABLE IF NOT EXISTS agentguard_datasets (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    name TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_datasets_workspace_id ON agentguard_datasets (workspace_id);

CREATE TABLE IF NOT EXISTS agentguard_dataset_versions (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES agentguard_datasets(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agentguard_dataset_versions_dataset_version
    ON agentguard_dataset_versions (dataset_id, version);

CREATE TABLE IF NOT EXISTS agentguard_dataset_examples (
    id TEXT PRIMARY KEY,
    dataset_version_id TEXT NOT NULL REFERENCES agentguard_dataset_versions(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    golden_status TEXT NOT NULL DEFAULT 'unvalidated',
    validation_confidence DOUBLE PRECISION,
    validation_reason TEXT NOT NULL DEFAULT '',
    validated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agentguard_dataset_examples_version_id
    ON agentguard_dataset_examples (dataset_version_id);

CREATE TABLE IF NOT EXISTS agentguard_evidence (
    id TEXT PRIMARY KEY,
    example_id TEXT NOT NULL REFERENCES agentguard_dataset_examples(id) ON DELETE CASCADE,
    source_ref TEXT NOT NULL,
    text TEXT NOT NULL,
    retrieval_score DOUBLE PRECISION NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cited_in_verdict BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_agentguard_evidence_example_id ON agentguard_evidence (example_id);

CREATE TABLE IF NOT EXISTS agentguard_recommendations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    recommendation JSONB NOT NULL DEFAULT '{}',
    reasoning TEXT NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_recommendations_workspace_kind
    ON agentguard_recommendations (workspace_id, kind);

CREATE TABLE IF NOT EXISTS agentguard_model_benchmarks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    dataset_version_id TEXT,
    suite_id TEXT NOT NULL,
    models JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_model_benchmarks_workspace_id
    ON agentguard_model_benchmarks (workspace_id);

CREATE TABLE IF NOT EXISTS agentguard_model_benchmark_results (
    id TEXT PRIMARY KEY,
    benchmark_id TEXT NOT NULL REFERENCES agentguard_model_benchmarks(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    example_id TEXT,
    evaluation_result_id TEXT NOT NULL,
    cost_usd DOUBLE PRECISION,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_model_benchmark_results_benchmark_id
    ON agentguard_model_benchmark_results (benchmark_id);

CREATE TABLE IF NOT EXISTS agentguard_judge_calibration_examples (
    id TEXT PRIMARY KEY,
    metric TEXT NOT NULL,
    workspace_id TEXT,
    case_input JSONB,
    case_actual_output JSONB,
    human_label JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agentguard_judge_calibration_examples_metric
    ON agentguard_judge_calibration_examples (metric);

-- Async job queue (0012_job_queue.sql) -------------------------------------
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

-- ModelBenchmarkResult.tokens_input (0013_benchmark_tokens_input.sql) -----
ALTER TABLE agentguard_model_benchmark_results ADD COLUMN IF NOT EXISTS tokens_input INTEGER;

-- EvaluationResult.source_step_id (0014_evaluation_result_source_step.sql) --
ALTER TABLE agentguard_evaluation_results ADD COLUMN IF NOT EXISTS source_step_id TEXT;

-- Recommendation.status/decided_at/decided_by (0017_recommendation_status.sql) --
ALTER TABLE agentguard_recommendations ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE agentguard_recommendations ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;
ALTER TABLE agentguard_recommendations ADD COLUMN IF NOT EXISTS decided_by TEXT;
