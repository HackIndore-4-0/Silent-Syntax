-- HumanDecision.modified_evidence — lets a reviewer approve an action
-- with corrected parameters (e.g. a counter-price) instead of only a
-- bare approve/reject, resumed via perform_action_with_result(). Purely
-- additive; `evidence` itself remains the immutable original proposal.

ALTER TABLE agentguard_human_decisions ADD COLUMN IF NOT EXISTS modified_evidence JSONB;
