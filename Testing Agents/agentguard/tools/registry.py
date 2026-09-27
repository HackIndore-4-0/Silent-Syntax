"""Tool registry — where an agent's tool callables and their registered
fallback ("alternative") tools live.

Explicit registration only (Rule: "Do not allow arbitrary tool
substitution — alternative tools must be explicitly registered/
configured"). `agentguard.call_tool()` (context.py) never falls back to
anything that wasn't registered here first.

In-process, module-level singleton — same pattern as
`_runtime.py`/`human/broker.py`'s `configure()`/`get_repository()`/
`get_broker()`. A real deployment could later persist alternative
registrations centrally (agentguard_tool_alternatives, see
storage/migrations/0004_phase4.sql) and load them into this registry at
process start; the registry itself stays the single source of truth
`call_tool()` consults synchronously, with no I/O.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..models import ToolAlternative

ToolCallable = Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._callables: dict[str, ToolCallable] = {}
        self._alternatives: dict[str, ToolAlternative] = {}

    def register_tool(self, name: str, fn: ToolCallable) -> None:
        """Associate `name` with the actual callable agentguard.call_tool()
        invokes. A tool must be registered before it can be called or
        used as a fallback."""
        self._callables[name] = fn

    def get_callable(self, name: str) -> ToolCallable | None:
        return self._callables.get(name)

    def register_alternative(self, primary: str, fallback: str, *, reliability_threshold: float = 0.8) -> ToolAlternative:
        alt = ToolAlternative(primary=primary, fallback=fallback, reliability_threshold=reliability_threshold)
        self._alternatives[primary] = alt
        return alt

    def get_alternative(self, primary: str) -> ToolAlternative | None:
        return self._alternatives.get(primary)

    def list_alternatives(self) -> list[ToolAlternative]:
        return list(self._alternatives.values())

    def list_tools(self) -> list[str]:
        return sorted(self._callables)


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def configure_registry(registry: ToolRegistry) -> None:
    global _registry
    _registry = registry


def reset_registry() -> None:
    global _registry
    _registry = None
