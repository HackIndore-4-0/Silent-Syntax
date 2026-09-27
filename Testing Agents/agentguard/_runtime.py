"""Internal wiring for the default repository used by @monitor when the
caller hasn't configured one explicitly.

Kept out of decorator.py so tests can swap in a fake repository via
agentguard.configure(...) without monkeypatching internals, and so
importing agentguard doesn't require asyncpg to be installed unless a
Postgres repository is actually needed (the import below is deferred
into get_repository()).
"""
from __future__ import annotations

from typing import Optional

from .storage.repository import RunRepository

_repository: Optional[RunRepository] = None


def configure(repository: RunRepository) -> None:
    """Register the repository @monitor should use.

    Call this once at process start (or from a test fixture) before any
    @monitor-wrapped function runs. Without it, the default Postgres
    repository (agentguard.storage.postgres.PostgresRunRepository) is
    created lazily from AGENTGUARD_DATABASE_URL on first use.
    """
    global _repository
    _repository = repository


def get_repository() -> RunRepository:
    global _repository
    if _repository is None:
        from .storage.postgres import PostgresRunRepository

        _repository = PostgresRunRepository.from_env()
    return _repository


def reset() -> None:
    """Test-only: clear the configured repository."""
    global _repository
    _repository = None
