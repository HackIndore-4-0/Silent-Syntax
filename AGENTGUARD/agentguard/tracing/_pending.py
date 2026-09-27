"""Tracked background tasks for the sync-@traceable/sync-LLM-client edge
case.

A *sync* @traceable function (or a synchronous LLM client's .create())
runs on a thread where an event loop is already active underneath it
(the caller's own loop for an async @monitor agent, or the fresh loop
asyncio.run() created for a fully-sync @monitor agent). From inside
that synchronous call frame you cannot `await` anything, and you must
not hop to a *different* thread's event loop to do the actual
repository write (PostgresRunRepository's asyncpg pool is bound to the
loop it was first created on).

The only safe option: schedule the recording coroutine as a task on the
*same, currently-running* loop (legal to call from sync code — it just
won't execute until control returns to the loop) and track it here so
`drain_pending_trace_writes()` can await it before the run completes.
`agentguard/decorator.py`'s `_execute` calls that once, in its existing
`finally` block, right before the run is considered done — otherwise
asyncio.run() would silently cancel any still-pending task at shutdown,
dropping the trace step.
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine

_pending: set[asyncio.Task] = set()


def track(task: asyncio.Task) -> None:
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def schedule(coro: Coroutine[Any, Any, Any]) -> None:
    """Schedule `coro` (a StepRecorder.finish_success()/finish_failure()
    call) on the currently-running loop and track it for draining.
    Safe to call from synchronous code: asyncio.get_running_loop()
    always succeeds here because tracing requires an active @monitor
    run, which guarantees a loop is already running underneath it."""
    task = asyncio.get_running_loop().create_task(coro)
    track(task)


async def drain_pending_trace_writes() -> None:
    if not _pending:
        return
    tasks = list(_pending)
    await asyncio.gather(*tasks, return_exceptions=True)
