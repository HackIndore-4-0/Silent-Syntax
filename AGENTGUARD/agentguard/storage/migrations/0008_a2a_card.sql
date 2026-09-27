-- A2A Agent Card migration (agentguard/a2a/card.py) — author-supplied
-- description/capabilities on an existing agentguard_agents row.
--
-- Purely additive: ADD COLUMN IF NOT EXISTS only. Both new columns are
-- honesty-constrained: AgentGuard has no way to derive an agent's real
-- capabilities from its runs, so `capabilities` defaults to an empty
-- array (never fabricated) and `description` defaults to NULL, both
-- set only via the new update_agent_card() repository method — never
-- by save_agent_registration()'s own INSERT (still "ON CONFLICT DO
-- NOTHING", unchanged).

ALTER TABLE agentguard_agents ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE agentguard_agents ADD COLUMN IF NOT EXISTS capabilities JSONB NOT NULL DEFAULT '[]'::jsonb;
