"""Shared TraceStep recording core for @traceable and wrap_llm_client.

Both primitives produce the same underlying row shape; this module is
the one place that knows how to build it, open its OTel span, persist
it, record its audit event, and — critically — never let a *recording*
failure (DB write, OTel export) propagate to break the user's agent
(logged loudly via logger.exception, never raised). The traced
function's own exceptions are a separate concern entirely: they are
captured for diagnostics and always re-raised unchanged by the callers
of this module (agentguard/tracing/traceable.py, llm_wrap.py).
"""
from __future__ import annotations

import inspect
import logging
import os
import sysconfig
import time
import traceback as traceback_module
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from .. import context as run_context
from .._runtime import get_repository
from ..models import TraceStep, new_run_id
from ..otel.instrumentation import start_trace_step_span
from .context import current_step_id, reset_current_step, set_current_step

logger = logging.getLogger("agentguard.tracing")

_PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../agentguard/
_STDLIB_DIR = os.path.abspath(sysconfig.get_paths()["stdlib"])


def _is_internal_frame(filename: str) -> bool:
    """True for a frame that is agentguard's own code, Python's
    standard library (notably contextlib's async-generator-to-context-
    manager machinery that `@asynccontextmanager` wraps traced_call()
    in — a real frame that sits between agentguard's own code and the
    user's, and must not be mistaken for the call site), or an
    installed dependency under site-packages."""
    return (
        filename.startswith(_PACKAGE_DIR)
        or filename.startswith(_STDLIB_DIR)
        or "site-packages" in filename
        or "dist-packages" in filename
    )


def _find_call_site() -> tuple[str | None, str | None, int | None]:
    """The first stack frame OUTSIDE agentguard/stdlib/dependencies —
    i.e. the user's own code that actually made this traced call.
    Populated for EVERY TraceStep (success or failure), unlike the
    exception-traceback-derived code_file/function/lineno below, which
    only exists when the call raised. A metric that scores a
    *successful* call poorly (the common case: no exception, just a
    bad answer) would otherwise have no code-location evidence at all
    — this is what lets agentguard.evaluation.diagnose point at a
    real line."""
    for frame_info in inspect.stack(0)[1:]:
        filename = os.path.abspath(frame_info.filename)
        if not _is_internal_frame(filename):
            return filename, frame_info.function, frame_info.lineno
    return None, None, None


def require_run() -> run_context.RunContext:
    """RuntimeError if called outside an active @monitor-wrapped run —
    a caller-error, always propagates, never swallowed. Mirrors
    agentguard/context.py's own _require_context()."""
    ctx = run_context.current_run()
    if ctx is None:
        raise RuntimeError(
            "agentguard tracing (@traceable / wrap_llm_client) requires an "
            "active @monitor-wrapped run"
        )
    return ctx


class StepRecorder:
    """One use per traced call. finish_success()/finish_failure() build
    the TraceStep, persist it, record its audit event, and close the
    span — all inside a try/except that swallows and logs any
    recording failure without ever touching the caller's own
    result/exception."""

    def __init__(self, ctx: run_context.RunContext, kind: str, name: str, call_input: dict[str, Any]) -> None:
        self.ctx = ctx
        self.kind = kind
        self.name = name
        self.call_input = call_input
        self.step_id = new_run_id()
        self.parent_step_id = current_step_id()
        self._t0 = time.monotonic()
        self.call_site_file, self.call_site_function, self.call_site_lineno = _find_call_site()

    async def finish_success(
        self,
        output: Any,
        *,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        cost_usd: float | None = None,
        model_name: str | None = None,
    ) -> None:
        await self._finish(
            outcome="success", output=output, exc=None,
            tokens_input=tokens_input, tokens_output=tokens_output, cost_usd=cost_usd, model_name=model_name,
        )

    async def finish_failure(self, exc: BaseException, *, model_name: str | None = None) -> None:
        await self._finish(outcome="failure", output=None, exc=exc, model_name=model_name)

    async def _finish(
        self,
        *,
        outcome: str,
        output: Any,
        exc: BaseException | None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        cost_usd: float | None = None,
        model_name: str | None = None,
    ) -> None:
        latency_ms = (time.monotonic() - self._t0) * 1000
        exception_type = exception_message = traceback_text = None
        code_file = code_function = None
        code_lineno: int | None = None

        if exc is not None:
            exception_type = type(exc).__name__
            exception_message = str(exc)
            traceback_text = "".join(
                traceback_module.format_exception(type(exc), exc, exc.__traceback__)
            )
            tb = exc.__traceback__
            last = tb
            while last is not None and last.tb_next is not None:
                last = last.tb_next
            if last is not None:
                code_file = last.tb_frame.f_code.co_filename
                code_function = last.tb_frame.f_code.co_name
                code_lineno = last.tb_lineno

        if code_file is None:
            # No exception (the common evaluation-failure case: the call
            # succeeded, it just scored badly) — fall back to the call
            # site captured at StepRecorder construction, so "where in
            # code" is never simply unanswerable for a successful call.
            code_file, code_function, code_lineno = self.call_site_file, self.call_site_function, self.call_site_lineno

        step = TraceStep(
            id=self.step_id,
            run_id=self.ctx.run.id,
            parent_step_id=self.parent_step_id,
            kind=self.kind,
            name=self.name,
            input=self.call_input,
            output=output,
            outcome=outcome,
            latency_ms=latency_ms,
            exception_type=exception_type,
            exception_message=exception_message,
            traceback_text=traceback_text,
            code_file=code_file,
            code_function=code_function,
            code_lineno=code_lineno,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_usd=cost_usd,
            model_name=model_name,
        )

        try:
            repository = get_repository()
            await repository.save_trace_step(step)
            self.ctx.record_event("TRACE_STEP", step.model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - a failed trace write must never crash the agent
            logger.exception(
                "agentguard.tracing: failed to record trace step %r (%s) for run %s",
                step.name, step.kind, self.ctx.run.id,
            )
            try:
                self.ctx.record_event(
                    "TRACE_WRITE_FAILED",
                    {"step_id": step.id, "kind": step.kind, "name": step.name},
                )
            except Exception:  # noqa: BLE001 - recording the failure event itself must never raise either
                logger.exception(
                    "agentguard.tracing: failed to record TRACE_WRITE_FAILED for run %s", self.ctx.run.id
                )


@asynccontextmanager
async def traced_call(kind: str, name: str, call_input: dict[str, Any]) -> AsyncIterator[StepRecorder]:
    """Sets the current-step ContextVar to this step's id for the
    duration of the wrapped call (so nested @traceable/LLM calls made
    from within it pick up the right parent_step_id) and opens a real
    nested OTel span. Requires an active run (see require_run());
    that RuntimeError is intentionally raised before this context
    manager's own try/finally, so it is never mistaken for a recording
    failure."""
    ctx = require_run()
    recorder = StepRecorder(ctx, kind, name, call_input)
    token = set_current_step(recorder.step_id)
    try:
        with start_trace_step_span(name, kind):
            yield recorder
    finally:
        reset_current_step(token)
