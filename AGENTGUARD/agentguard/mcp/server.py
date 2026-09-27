"""A minimal, read-only MCP server exposing this workspace's own
governance data — run history, reliability reports, audit-chain
verification, tool reliability profiles — for an external MCP host
(Claude Desktop, Claude Code, an internal ops bot) to query.

Lives here, not in server/ — that package is specifically the REST/
WebSocket transport for the dashboard; this is a different transport
(MCP stdio) for the exact same engines, calling straight into
agentguard.* the same way agentguard/cli/__init__.py's existing
commands already do.

Deliberately read-only in v1: no rollback, no policy edits. Handing an
MCP host (typically driven by an LLM's own judgement about when to
call a tool) a mutating operation with no human-approval gate in the
loop would be a governance regression for a governance product.

Auth: stdio has no per-request headers, so there is no per-call
bearer-token story — the workspace is resolved ONCE at process startup
(see `agentguard mcp-serve` in agentguard/cli/__init__.py) via the same
agentguard.auth.service.authenticate_api_key() the dashboard's own
bearer-token path already uses, and captured in a closure. No tool
below accepts a workspace_id argument, so an MCP client can never ask
for a different workspace's data.

Uses FastMCP (PyPI `fastmcp`, pyproject.toml's `mcp` extra) — verified
against the real, installed package: `fastmcp.FastMCP`, the same
`@mcp.tool()` decorator style as the lower-level `mcp.server.MCPServer`
it replaces, and `await mcp.run_stdio_async()` as the async entry point
(mcp.run(transport="stdio") is the sync equivalent, used nowhere here
so the whole CLI command can share one asyncio.run() call).
"""
from __future__ import annotations

from typing import Any


def build_server(repository: Any, workspace_id: str) -> Any:
    """Build (but do not run) a FastMCP server exposing the four
    read-only governance tools, scoped to `workspace_id`. Synchronous —
    building the server and registering its (async) tool functions
    requires no I/O itself; only the tool calls do, once the server is
    actually run."""
    from fastmcp import FastMCP

    from ..audit.chain import verify_audit_chain as _verify_audit_chain
    from ..reliability.report import build_reliability_report
    from ..reliability.tool_profile import ToolProfileEngine

    mcp = FastMCP("agentguard")

    async def _owned_run(run_id: str) -> dict[str, Any]:
        run = await repository.get_run(run_id)
        if run is None or run.get("workspace_id") != workspace_id:
            raise ValueError(f"run {run_id!r} not found in this workspace")
        return run

    @mcp.tool()
    async def list_recent_runs(agent_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """List this workspace's most recent runs, most recent first,
        optionally filtered to one agent_name."""
        runs = await repository.list_runs(limit=limit, workspace_id=workspace_id)
        if agent_name is not None:
            runs = [r for r in runs if r.get("agent_name") == agent_name]
        return runs

    @mcp.tool()
    async def get_reliability_report(run_id: str) -> dict[str, Any]:
        """The six-dimension Reliability Report for one run in this
        workspace (Correctness, Goal Completion, Constraint Adherence,
        Decision Consistency, Tool Usage, Behavioral Reliability)."""
        run = await _owned_run(run_id)
        risk_assessments = await repository.list_risk_assessments(run_id)
        root_cause = await repository.get_root_cause(run_id)
        audit_events = await repository.list_audit_events(run_id)
        report = build_reliability_report(
            run, risk_assessments=risk_assessments, root_cause=root_cause, audit_events=audit_events
        )
        return report.to_dict()

    @mcp.tool()
    async def verify_audit_chain(run_id: str) -> dict[str, Any]:
        """Recompute this run's SHA-256 hash chain and report whether
        it is intact, or the first tampered/broken event."""
        await _owned_run(run_id)
        return await _verify_audit_chain(repository, run_id)

    @mcp.tool()
    async def list_tool_reliability(tool_name: str | None = None) -> list[dict[str, Any]]:
        """Reliability profile(s) (success/failure/timeout rates,
        latency percentiles, RELIABLE/DEGRADED/UNRELIABLE/
        INSUFFICIENT_DATA classification) for tools called from this
        workspace's runs."""
        names = await repository.list_tools(workspace_id=workspace_id)
        if tool_name is not None:
            names = [n for n in names if n == tool_name]
        engine = ToolProfileEngine(repository)
        return [(await engine.profile(name)).model_dump(mode="json") for name in names]

    return mcp
