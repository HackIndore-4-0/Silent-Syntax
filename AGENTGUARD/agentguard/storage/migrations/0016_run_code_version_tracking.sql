-- Run.git_commit_sha/branch/dirty/remote, dependency_lockfile_hash/path,
-- sdk_version — automatic, best-effort "what code produced this run?"
-- capture (agentguard/versioning.py, wired in from decorator.py). All
-- nullable: absent whenever the calling app's cwd isn't a git repo or
-- has no recognized lockfile, matching agent_version's existing shape.
-- Purely additive.

ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS git_commit_sha TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS git_branch TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS git_dirty BOOLEAN;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS git_remote TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS dependency_lockfile_hash TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS dependency_lockfile_path TEXT;
ALTER TABLE agentguard_runs ADD COLUMN IF NOT EXISTS sdk_version TEXT;
