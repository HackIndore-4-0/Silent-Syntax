-- Evaluation Platform — Phases 3 (golden dataset validation), 7
-- (recommendation), 8 (model benchmarking), 9 (evaluator-of-evaluators).
-- Purely additive. Every table uses a synthetic `id TEXT PRIMARY KEY` —
-- none need a composite key over a nullable workspace_id.

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
