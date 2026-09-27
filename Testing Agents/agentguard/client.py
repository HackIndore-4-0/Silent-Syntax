"""`AgentGuard` — the authenticated SDK client (Dashboard V2).

    from agentguard import AgentGuard

    guard = AgentGuard(api_key=os.getenv("AGENTGUARD_API_KEY"), project="production")

    @guard.monitor
    def my_agent():
        ...

    # or, loading the key from the environment automatically:
    guard = AgentGuard()

Architecture note (documented explicitly — see docs/DASHBOARD_V2_REPORT.md
§Known limitations): AgentGuard's SDK and its storage have always shared
one `RunRepository` in-process (see `agentguard._runtime.get_repository()`
— the design every phase since Phase 1 has built on; Rule 19 of the
Dashboard V2 spec: "Do not replace the existing AgentGuard architecture
unnecessarily"). `AgentGuard(api_key=...)` therefore resolves the API
key against THAT SAME repository (in-process, synchronous with the
call), rather than opening a new network connection to a separate
ingestion server — there is no separate AgentGuard server process this
SDK talks to over HTTP in this architecture; the FastAPI app
(`server/api.py`) and this client both read/write the one configured
repository. What the key genuinely authenticates and enforces is real:
a revoked or unknown key raises `AuthenticationError` immediately, and
every run `@guard.monitor` produces is permanently tagged with the
resolved workspace/project — never sent anywhere unauthenticated, and
never visible to a different workspace's queries (see
`agentguard.storage.repository.RunRepository`'s `workspace_id`
parameters, enforced at the query level in
`agentguard.storage.memory`/`agentguard.storage.postgres`).

`@guard.monitor` reuses the EXACT existing `agentguard.decorator.monitor`
pipeline (Decision Engine, Root Cause, Risk, Checkpoints, Audit chain,
Tool Profiles, ...) — this class adds authentication and tenancy
tagging only; it does not reimplement or duplicate any execution logic.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, TypeVar

from .auth.security import hash_token
from .decorator import monitor as _monitor
from .evaluators.base import Evaluator
from .evaluators.llm_judge import AsyncEvaluator
from .models import Policy

F = TypeVar("F", bound=Callable[..., Any])


class AuthenticationError(ValueError):
    """Raised when `api_key` is missing, malformed, unknown, or revoked."""


class AgentGuard:
    """An authenticated handle to one workspace/project, resolved once
    from an API key at construction time.

    `api_key` defaults to `AGENTGUARD_API_KEY` from the environment —
    never hard-code a key or read it from frontend JavaScript (Rule:
    "API keys belong to server-side agent code/environment").
    """

    def __init__(self, api_key: str | None = None, project: str = "production") -> None:
        self.api_key = api_key or os.environ.get("AGENTGUARD_API_KEY")
        if not self.api_key:
            raise AuthenticationError(
                "AgentGuard(api_key=...) was not given a key and AGENTGUARD_API_KEY is not set in the environment"
            )
        self.project_name = project
        self.user_id: str | None = None
        self.workspace_id: str | None = None
        self.project_id: str | None = None
        self._resolve()

    def _resolve(self) -> None:
        from ._runtime import get_repository
        from .auth.service import resolve_project

        repository = get_repository()

        async def _do_resolve() -> None:
            key_data = await repository.get_api_key_by_hash(hash_token(self.api_key))
            if key_data is None:
                raise AuthenticationError("invalid API key")
            if key_data.get("status") != "active" or key_data.get("revoked_at") is not None:
                raise AuthenticationError("this API key has been revoked")
            await repository.update_api_key_last_used(key_data["id"])

            self.user_id = key_data["user_id"]
            self.workspace_id = key_data["workspace_id"]
            project = await resolve_project(repository, workspace_id=self.workspace_id, project_name=self.project_name)
            self.project_id = project.id

        _run_sync(_do_resolve())

    def monitor(
        self,
        func: F | None = None,
        *,
        policy: Policy | None = None,
        evaluators: list[Evaluator] | None = None,
        llm_judge: AsyncEvaluator | bool | None = True,
        agent_version: str | None = None,
    ) -> Any:
        """Identical to `agentguard.monitor`, except every run it
        produces is tagged with this client's authenticated
        workspace_id/project_id (see module docstring)."""
        return _monitor(
            func,
            policy=policy,
            evaluators=evaluators,
            llm_judge=llm_judge,
            agent_version=agent_version,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
        )

    def trace(
        self,
        func: F | None = None,
        *,
        policy: Policy | None = None,
        metrics: list[Any] | dict[str, Any] | bool | None = True,
        llm_judge: AsyncEvaluator | bool | None = True,
        agent_version: str | None = None,
    ) -> Any:
        """Identical to `agentguard.trace` (full monitoring + the
        builtin evaluation-metric suite), tagged with this client's
        authenticated workspace_id/project_id — the one-decorator
        quickstart for a run that should show up in the connected
        AgentGuard dashboard."""
        return _monitor(
            func,
            policy=policy,
            llm_judge=llm_judge,
            agent_version=agent_version,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            auto_evaluate=metrics,
        )


def _run_sync(coro: Any) -> None:
    """`AgentGuard.__init__` is synchronous (matches the spec's own
    usage example — no `await AgentGuard(...)`), but key resolution needs
    the async repository. Runs its own short-lived event loop when none
    is already running; raises clearly if called from inside an
    already-running loop.

    Deliberately does NOT run the resolution on a background thread as a
    workaround: `PostgresRunRepository`'s connection pool is bound to the
    event loop it was first created on (asyncpg pools are not meant to
    be shared across loops/threads), so silently hopping threads here
    could corrupt that pool in production. Construct `AgentGuard(...)`
    once, synchronously, before entering async code (e.g. at module
    scope, or in a sync `main()` before `asyncio.run(...)`) — the same
    constraint `@monitor` on a sync function already documents."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    raise RuntimeError(
        "AgentGuard(...) cannot be constructed from inside a running event loop — "
        "construct it once, synchronously, before entering async code (e.g. at module scope)."
    )
