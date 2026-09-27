"""MCP client adapter — registers an external MCP server's tools into
agentguard.tools.ToolRegistry.

agentguard/tools/registry.py is deliberately "in-process, no I/O" (its
own docstring's words) — subprocess/network transport code belongs
here instead, mirroring how agentguard/llm/ hosts an optional
external-service integration (AnthropicProvider) separately from core.

Uses FastMCP (PyPI `fastmcp`, pyproject.toml's `mcp` extra) rather than
the lower-level official `mcp` SDK directly — FastMCP's `Client` +
`StdioTransport` wrap the same underlying transport with a friendlier
API (`result.data` gives back the tool's return value already
deserialized, no manual content-block parsing needed for the common
case). Verified against the real, installed `fastmcp` package (v4.x)
rather than assumed.

`Client(...)` is an async context manager; `async with Client(...) as
client` is its whole connection lifecycle (launches the subprocess,
negotiates the protocol, and on exit disconnects). Held open here
across multiple calls via an AsyncExitStack rather than one-shot per
call, since a stdio subprocess is far too slow to spawn per tool
invocation and would pollute latency-based reliability profiling with
process-startup noise.

Tool naming: every registered tool is prefixed `<prefix>__<tool.name>`,
mirroring the `mcp__<server>__<tool>` convention already visible in
Claude Code's own MCP tool namespace.

Argument shape: MCP tools take one dict of named arguments per their
JSON Schema (`tool.input_schema`) — there is no positional-argument
concept. Every synthesized wrapper is therefore `async def wrapper(**kwargs)`
(no `*args`); calling one through `agentguard.call_tool()` must always
use keyword arguments.

Error/timeout handling requires no change to agentguard/context.py:
call_tool()'s existing timeout_s/outcome recording, ToolProfileEngine
reliability profiling, and register_alternative() fallback all keep
working completely unmodified — an MCP-backed tool just looks like any
other registered callable that sometimes raises.

Environment note (a real gotcha hit while building this): FastMCP's
StdioTransport ultimately builds an `mcp.StdioServerParameters(env=...)`
— passing `env=None` does NOT inherit the current process's
environment (unlike Python's own `subprocess` module, whose `env=None`
default does inherit). register_mcp_server() below always merges the
current process's `os.environ` with any caller-supplied `env=`
overrides, since "the spawned server can't see AGENTGUARD_DATABASE_URL/
API keys/etc. that are obviously already in my own environment" is a
much more surprising default than the reverse.
"""
from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from typing import Any, Sequence

from ..tools.registry import ToolRegistry, get_registry

logger = logging.getLogger("agentguard.mcp")


class MCPToolError(Exception):
    """Raised when an MCP tool call completes (no transport/subprocess
    failure) but the server itself reports CallToolResult.is_error —
    otherwise call_tool() would record a false outcome="success"."""


class _McpConnection:
    def __init__(self, exit_stack: AsyncExitStack, client: Any) -> None:
        self.exit_stack = exit_stack
        self.client = client


_connections: dict[str, _McpConnection] = {}


def _default_prefix(command: str) -> str:
    return command.replace("\\", "/").rsplit("/", 1)[-1]


def _extract_text(content: Any) -> str | None:
    parts = [getattr(block, "text", None) for block in (content or [])]
    parts = [p for p in parts if p is not None]
    return "\n".join(parts) if parts else None


def _make_tool_wrapper(client: Any, mcp_tool_name: str, call_timeout_s: float):
    async def wrapper(**kwargs: Any) -> Any:
        result = await client.call_tool(
            mcp_tool_name, kwargs, timeout=call_timeout_s, raise_on_error=False
        )
        if result.is_error:
            raise MCPToolError(_extract_text(result.content) or f"MCP tool {mcp_tool_name!r} reported an error")
        data = getattr(result, "data", None)
        if data is not None:
            return data
        text = _extract_text(result.content)
        return text if text is not None else getattr(result, "structured_content", None)

    wrapper.__name__ = mcp_tool_name
    wrapper.__qualname__ = mcp_tool_name
    return wrapper


async def register_mcp_server(
    command: str,
    *,
    args: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    registry: ToolRegistry | None = None,
    prefix: str | None = None,
    call_timeout_s: float = 30.0,
) -> list[str]:
    """Spawn `command` as a stdio MCP server, discover its tools, and
    register each as `<prefix>__<tool.name>` on `registry` (or the
    global registry). Returns the list of registered (prefixed) names.
    The connection is held open (see module docstring) until
    close_mcp_server(prefix)/reset_mcp_connections() is called.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    resolved_registry = registry or get_registry()
    resolved_prefix = prefix or _default_prefix(command)
    merged_env = {**os.environ, **(env or {})}

    transport = StdioTransport(command=command, args=list(args or []), env=merged_env)

    exit_stack = AsyncExitStack()
    try:
        client = await exit_stack.enter_async_context(Client(transport))
    except Exception:
        await exit_stack.aclose()
        raise
    _connections[resolved_prefix] = _McpConnection(exit_stack, client)

    registered: list[str] = []
    for tool in await client.list_tools():
        name = f"{resolved_prefix}__{tool.name}"
        resolved_registry.register_tool(name, _make_tool_wrapper(client, tool.name, call_timeout_s))
        registered.append(name)
    return registered


async def close_mcp_server(prefix: str) -> None:
    conn = _connections.pop(prefix, None)
    if conn is not None:
        await conn.exit_stack.aclose()


async def reset_mcp_connections() -> None:
    for prefix in list(_connections.keys()):
        await close_mcp_server(prefix)
