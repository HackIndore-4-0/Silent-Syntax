-- EvaluationResult.source_step_id — links a scored metric back to the
-- specific TraceStep whose output it scored (agentguard/evaluation/diagnose.py
-- needs this to find the real prompt/code location behind a failing
-- score). Purely additive.

ALTER TABLE agentguard_evaluation_results ADD COLUMN IF NOT EXISTS source_step_id TEXT;
