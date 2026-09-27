"""Automatic git-commit and dependency-lockfile capture for run
reproducibility — "what code produced this run?" (see Run.git_commit_sha
etc. in agentguard/models.py, wired in from agentguard/decorator.py).

Shells out to `git` relative to the CALLING APPLICATION's own working
directory, not agentguard's own package directory — @monitor is used by
external code (a RAG pipeline, a chat agent, ...), so the repo that
matters is theirs. Never raises: a run in a non-git directory, or a
machine without git installed, just gets None metadata — reproducibility
capture is best-effort and must never be a reason to crash the agent.

Cached per (resolved) cwd for the process lifetime, so a long-running
process handling many runs shells out to git/hashes the lockfile once,
not per run (Rule: "must NOT significantly increase agent latency").
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitMetadata:
    commit_sha: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    remote: str | None = None


@dataclass
class DependencySnapshot:
    lockfile_path: str | None = None
    lockfile_hash: str | None = None


_LOCKFILE_NAMES = ("uv.lock", "poetry.lock", "requirements.txt", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")

_git_cache: dict[str, GitMetadata | None] = {}
_dependency_cache: dict[str, DependencySnapshot | None] = {}


def _run_git(args: list[str], cwd: str) -> str | None:
    """None means the command failed to run at all (not a git repo, git
    not installed, or timed out) — distinct from an empty-but-successful
    stdout (e.g. `git status --porcelain` on a clean working tree, which
    callers must not mistake for a failure)."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_git_metadata(cwd: str | None = None) -> GitMetadata | None:
    """Best-effort git commit/branch/dirty/remote for `cwd` (default: the
    process's current working directory). Returns None entirely if `cwd`
    isn't inside a git repo or `git` isn't installed."""
    resolved_cwd = cwd or str(Path.cwd())
    if resolved_cwd in _git_cache:
        return _git_cache[resolved_cwd]

    commit_sha = _run_git(["rev-parse", "HEAD"], resolved_cwd)
    if not commit_sha:
        _git_cache[resolved_cwd] = None
        return None

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], resolved_cwd)
    dirty_output = _run_git(["status", "--porcelain"], resolved_cwd)
    remote = _run_git(["remote", "get-url", "origin"], resolved_cwd)

    metadata = GitMetadata(
        commit_sha=commit_sha,
        branch=branch or None,
        dirty=(dirty_output != "") if dirty_output is not None else None,
        remote=remote or None,
    )
    _git_cache[resolved_cwd] = metadata
    return metadata


def get_dependency_snapshot(cwd: str | None = None) -> DependencySnapshot | None:
    """SHA-256 of the first recognized dependency lockfile found directly
    in `cwd` (default: the process's current working directory). Returns
    None if no known lockfile is present — never guesses at dependency
    versions from an absent file."""
    resolved_cwd = cwd or str(Path.cwd())
    if resolved_cwd in _dependency_cache:
        return _dependency_cache[resolved_cwd]

    snapshot = None
    for name in _LOCKFILE_NAMES:
        candidate = Path(resolved_cwd) / name
        if candidate.is_file():
            snapshot = DependencySnapshot(
                lockfile_path=name,
                lockfile_hash=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            )
            break
    _dependency_cache[resolved_cwd] = snapshot
    return snapshot


def reset_versioning_cache() -> None:
    """Test-only: clear the per-cwd caches so a test can exercise a fresh
    subprocess/filesystem read after changing state on disk."""
    _git_cache.clear()
    _dependency_cache.clear()
