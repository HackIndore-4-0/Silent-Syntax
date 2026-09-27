"""@traceable — fine-grained tracing for any function called during an
already-@monitor-wrapped run.

Requires an active run (RuntimeError otherwise, exactly like
update_state()/perform_action()) and works on both sync and async
functions, mirroring @monitor's own is_coroutine dispatch
(agentguard/decorator.py) — but, unlike @monitor's sync path, never
spins up its own event loop: it always runs inside one that's already
live under the enclosing @monitor call. See agentguard/tracing/_pending.py
for how the sync path's recording (an async repository write) gets
persisted without ever blocking or re-entering that live loop.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, TypeVar

from . import _pending
from .context import reset_current_step, set_current_step
from .recording import StepRecorder, require_run, traced_call

F = TypeVar("F", bound=Callable[..., Any])


def traceable(func: F) -> F:
    """Record one TraceStep (kind="function") per call to the wrapped
    function: full args/kwargs, return value, latency, and — on
    exception — type/message/traceback/code-location, then always
    re-raises the original exception unchanged.

    Nests automatically: a @traceable function calling another
    @traceable function (or triggering a wrap_llm_client-wrapped LLM
    call) gets its parent_step_id set with no action from the caller.
    """
    name = getattr(func, "__qualname__", getattr(func, "__name__", "traced_function"))
    is_coroutine = inspect.iscoroutinefunction(func)

    if is_coroutine:

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            call_input = {"args": list(args), "kwargs": kwargs}
            async with traced_call("function", name, call_input) as step:
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    await step.finish_failure(exc)
                    raise
                await step.finish_success(result)
                return result

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        call_input = {"args": list(args), "kwargs": kwargs}
        ctx = require_run()
        recorder = StepRecorder(ctx, "function", name, call_input)
        token = set_current_step(recorder.step_id)
        try:
            from ..otel.instrumentation import start_trace_step_span

            with start_trace_step_span(name, "function"):
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    _pending.schedule(recorder.finish_failure(exc))
                    raise
                _pending.schedule(recorder.finish_success(result))
                return result
        finally:
            reset_current_step(token)

    return sync_wrapper  # type: ignore[return-value]
