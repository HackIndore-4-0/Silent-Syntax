-- ModelBenchmarkEngine's best_long_context objective needs a real
-- (never fabricated) input-token count per benchmark result row.
-- Purely additive.

ALTER TABLE agentguard_model_benchmark_results ADD COLUMN IF NOT EXISTS tokens_input INTEGER;
