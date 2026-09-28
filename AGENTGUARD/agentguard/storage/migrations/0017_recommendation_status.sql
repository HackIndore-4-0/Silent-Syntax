-- Recommendation.status/decided_at/decided_by — an accept/reject
-- decision on a stored Recommendation (model recs from
-- ModelBenchmarkEngine.recommend(), Phase 7/8). Purely additive;
-- existing rows default to 'pending' so nothing already stored
-- silently becomes "decided".

ALTER TABLE agentguard_recommendations ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE agentguard_recommendations ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;
ALTER TABLE agentguard_recommendations ADD COLUMN IF NOT EXISTS decided_by TEXT;
