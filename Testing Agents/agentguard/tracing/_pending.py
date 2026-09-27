"""Tracked background tasks for the sync-@traceable/sync-LLM-client edge
case.

A *sync* @traceable function (or a synchronous LLM client's .create())
usually runs on the thread where an event loop is already active
underneath it (the caller's own loop for an async @monitor agent, or
the fresh loop asyncio.run() created for a fully-sync @monitor agent).
From inside that synchronous call frame you cannot `await` anything,
and you must not hop to a *different* thread's event loop to do the
actual repository write (PostgresRunRepository's asyncpg pool is bound
to the loop it was first created on).

The common case: schedule the recording coroutine as a task on the
*same, currently-running* loop (legal to call from sync code — it just
won't execute until control returns to the loop) and track it here so
`drain_pending_trace_writes()` can await it before the run completes.
`agentguard/decorator.py`'s `_execute` calls that once, in its existing
`finally` block, right before the run is considered done — otherwise
asyncio.run() would silently cancel any still-pending task at shutdown,
dropping the trace step.

The other case: a third-party framework invoked the sync `@traceable`
function from a WORKER THREAD it manages itself (e.g. LangChain's
`run_in_executor`, which runs a sync tool via
`loop.run_in_executor(None, ...)`). Contextvars still resolve correctly
there — `run_in_executor` explicitly copies the context
(`contextvars.copy_context().run(...)`) — but `asyncio.get_running_loop()`
genuinely has no loop to return in that thread, so the common path above
would raise. `schedule()` detects this and reschedules onto the run's
OWN loop (captured on `RunContext.loop` at the top of `_execute`) via
`run_coroutine_threadsafe` instead — never a fresh loop in the worker
thread, which `PostgresRunRepository`'s asyncpg pool could never safely
use anyway.
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine

_pending: set[asyncio.Future] = set()


def track(task: asyncio.Future) -> None:
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def schedule(coro: Coroutine[Any, Any, Any]) -> None:
    """Schedule `coro` (a StepRecorder.finish_success()/finish_failure()
    call) so it eventually runs on the run's own loop, and track it for
    draining. Safe to call from synchronous code on that same loop's
    thread (the common case), or from a worker thread a framework used
    to invoke the sync @traceable function (the LangChain-style case)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        from .. import context as run_context

        ctx = run_context.current_run()
        if ctx is None or ctx.loop is None:
            raise
        future = asyncio.run_coroutine_threadsafe(coro, ctx.loop)
        track(asyncio.wrap_future(future, loop=ctx.loop))
        return

    task = loop.create_task(coro)
    track(task)


async def drain_pending_trace_writes() -> None:
    if not _pending:
        return
    tasks = list(_pending)
    await asyncio.gather(*tasks, return_exceptions=True)
