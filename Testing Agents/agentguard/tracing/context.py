"""Ambient "current trace step" tracking.

Lets nested @traceable / wrap_llm_client calls set their own
parent_step_id automatically, with zero API surface for the caller to
manage nesting manually. A single ContextVar[str | None] holding "the
id of the trace step currently in progress" (not a manual stack) is
sufficient: .set()/.reset(token) already nests correctly across
`with`/`async with` blocks and gives every concurrent asyncio task its
own independent value — exactly like agentguard/context.py's `_current`
does for RunContext (context.py:53-64), whose shape this mirrors.
"""
from __future__ import annotations

import contextvars

_current_step_id: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "agentguard_current_trace_step_id", default=None
)


def current_step_id() -> str | None:
    return _current_step_id.get()


def set_current_step(step_id: str | None) -> contextvars.Token:
    return _current_step_id.set(step_id)


def reset_current_step(token: contextvars.Token) -> None:
    _current_step_id.reset(token)
