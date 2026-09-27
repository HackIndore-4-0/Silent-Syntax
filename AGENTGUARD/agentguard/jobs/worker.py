"""Worker — claims jobs via `repository.claim_next_job()` (Postgres: a
real `FOR UPDATE SKIP LOCKED` query, so multiple `agentguard worker`
processes can run side by side without double-claiming) and dispatches
to a registered handler by `kind`.

Why this is a SEPARATE mechanism from the sync-path `_pending` task
tracker already in `agentguard/tracing/_pending.py`: that one is
correct for "finish this one background thing before the run's own
`finally` block returns" (seconds, in-process, tied to one run's
lifetime). A dataset validation or a large model benchmark is out of
scope for any single process's lifetime — it needs a durable queue (a
crash or restart mid-job resumes from a `pending` row, not a silent
drop of in-flight work).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("agentguard.jobs")

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


class Worker:
    def __init__(self, repository: Any, handlers: dict[str, JobHandler]) -> None:
        self._repository = repository
        self._handlers = handlers

    async def run_once(self) -> bool:
        """Claims and runs at most one job. Returns True if a job was
        claimed (whether it then succeeded or failed), False if the
        queue was empty for every registered kind.

        With NO handlers registered, this claims nothing at all — a
        worker with an empty handler map must never claim (and thereby
        permanently fail, since `kinds=None` claims across every kind)
        jobs meant for a different worker process that registers a
        different handler subset."""
        if not self._handlers:
            return False
        job = await self._repository.claim_next_job(kinds=list(self._handlers))
        if job is None:
            return False

        handler = self._handlers.get(job["kind"])
        if handler is None:
            await self._repository.fail_job(job["id"], f"no handler registered for kind={job['kind']!r}", retry=False)
            return True

        try:
            await handler(job["payload"])
        except Exception as exc:
            logger.exception("job %s (kind=%s) failed", job["id"], job["kind"])
            await self._repository.fail_job(job["id"], str(exc), retry=True)
        else:
            await self._repository.complete_job(job["id"])
        return True

    async def run_forever(self, *, poll_interval_s: float = 2.0, stop_event: asyncio.Event | None = None) -> None:
        """Polls until `stop_event` is set (or forever, if none is
        given) — a plain poll loop, not a push/notify mechanism,
        matching this codebase's existing preference for explicit,
        inspectable mechanisms over hidden magic (see the MCP client's
        AsyncExitStack-held connections, the sync-path `_pending`
        tracker)."""
        while stop_event is None or not stop_event.is_set():
            claimed = await self.run_once()
            if not claimed:
                await asyncio.sleep(poll_interval_s)
