"""MCP server (agentguard/mcp/server.py) — the four read-only
governance tools, tested through FastMCP's own in-process `Client`
(no real stdio subprocess needed for correctness — that's what
FastMCP.run_stdio_async() itself provides, already covered by the
`fastmcp` package's own test suite, not ours to re-test)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client

import agentguard
from agentguard import Policy, monitor
from agentguard.mcp.server import build_server

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def _seed_run(repo, *, workspace_id: str, agent_name: str = "billing_agent") -> str:
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    existing = set(repo.runs.keys())
    await agent("task")
    run_id = next(rid for rid in repo.runs if rid not in existing)
    run = repo.runs[run_id]
    run.workspace_id = workspace_id
    run.agent_name = agent_name
    return run_id


async def test_list_recent_runs_scoped_to_workspace(fake_repository):
    run_a = await _seed_run(fake_repository, workspace_id="ws-a")
    run_b = await _seed_run(fake_repository, workspace_id="ws-b")

    mcp = build_server(fake_repository, "ws-a")
    async with Client(mcp) as client:
        result = await client.call_tool("list_recent_runs", {})
    run_ids = {r["id"] for r in result.data}
    assert run_a in run_ids
    assert run_b not in run_ids


async def test_get_reliability_report_rejects_foreign_workspace_run(fake_repository):
    run_a = await _seed_run(fake_repository, workspace_id="ws-a")

    mcp = build_server(fake_repository, "ws-b")
    async with Client(mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("get_reliability_report", {"run_id": run_a})


async def test_get_reliability_report_returns_real_report_for_own_workspace(fake_repository):
    run_a = await _seed_run(fake_repository, workspace_id="ws-a")

    mcp = build_server(fake_repository, "ws-a")
    async with Client(mcp) as client:
        result = await client.call_tool("get_reliability_report", {"run_id": run_a})
    assert "constraint_adherence" in result.data["dimensions"]


async def test_verify_audit_chain_reports_intact(fake_repository):
    run_a = await _seed_run(fake_repository, workspace_id="ws-a")

    mcp = build_server(fake_repository, "ws-a")
    async with Client(mcp) as client:
        result = await client.call_tool("verify_audit_chain", {"run_id": run_a})
    assert result.data["intact"] is True


async def test_list_tool_reliability_scoped_to_workspace(fake_repository):
    from agentguard.tools.registry import ToolRegistry, configure_registry, reset_registry

    registry = ToolRegistry()
    configure_registry(registry)

    async def flaky(**kwargs):
        raise RuntimeError("down")

    registry.register_tool("search_api", flaky)

    @monitor(policy=Policy(max_cost=60000, on_tool_exhausted="replan", max_replans=0), llm_judge=False)
    async def agent(task: str) -> str:
        return await agentguard.context.call_tool("search_api", primary_attempts=1)

    existing = set(fake_repository.runs.keys())
    with pytest.raises(Exception):
        await agent("task")
    run_id = next(rid for rid in fake_repository.runs if rid not in existing)
    fake_repository.runs[run_id].workspace_id = "ws-a"

    mcp = build_server(fake_repository, "ws-a")
    async with Client(mcp) as client:
        result = await client.call_tool("list_tool_reliability", {})
    assert any(p["tool"] == "search_api" for p in result.data)

    mcp_other = build_server(fake_repository, "ws-b")
    async with Client(mcp_other) as client_other:
        result_other = await client_other.call_tool("list_tool_reliability", {})
    assert result_other.data == []

    reset_registry()
