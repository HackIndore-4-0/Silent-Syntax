"""MCP client adapter (agentguard/mcp/client.py) — register_mcp_server().

Uses a hand-built fake FastMCP client/transport (no real MCP server, no
subprocess) so these tests never depend on network access or an
external server being reachable — only on the `fastmcp` package (the
`mcp` extra) being importable for its public Client/StdioTransport
names to monkeypatch.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastmcp")

import agentguard
from agentguard import Policy, monitor
from agentguard.mcp import MCPToolError, register_mcp_server, reset_mcp_connections
from agentguard.tools.registry import ToolRegistry, configure_registry, reset_registry

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository
    from agentguard.human.broker import reset_broker

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    reset_registry()
    yield repo
    reset_repository()
    reset_broker()
    reset_registry()


@pytest.fixture(autouse=True)
async def _reset_mcp_connections_after():
    yield
    await reset_mcp_connections()


class _FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: dict | None = None) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeCallToolResult:
    def __init__(self, *, data=None, text: str | None = None, is_error: bool = False, structured_content=None) -> None:
        self.content = [_FakeTextBlock(text)] if text is not None else []
        self.is_error = is_error
        self.data = data
        self.structured_content = structured_content


class _FakeFastMcpClient:
    """Stands in for `fastmcp.Client` — an async context manager whose
    __aenter__ returns the object list_tools()/call_tool() are called
    on, matching `client = await exit_stack.enter_async_context(Client(...))`."""

    instances: list["_FakeFastMcpClient"] = []

    def __init__(self, transport) -> None:
        self.transport = transport
        self.calls: list[tuple[str, dict]] = []
        self.tools: list[_FakeTool] = [_FakeTool("echo", "Echoes its input", {"type": "object"})]
        self.responses: dict[str, _FakeCallToolResult] = {}
        self.hang_on: set[str] = set()
        _FakeFastMcpClient.instances.append(self)

    async def __aenter__(self) -> "_FakeFastMcpClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def list_tools(self) -> list[_FakeTool]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict, *, timeout=None, raise_on_error: bool = True):
        self.calls.append((name, arguments))
        if name in self.hang_on:
            # Simulate the real SDK's own timeout enforcement (this fake
            # fully controls both sides, so it must actually raise on
            # timeout itself, not just sleep past it).
            if timeout is not None:
                await asyncio.wait_for(asyncio.sleep(999), timeout=timeout)
            else:
                await asyncio.sleep(999)
        return self.responses.get(name, _FakeCallToolResult(data="ok", text="ok"))


class _FakeStdioTransport:
    def __init__(self, *, command, args=None, env=None):
        self.command = command
        self.args = args
        self.env = env


@pytest.fixture(autouse=True)
def _patch_fastmcp(monkeypatch):
    import fastmcp
    import fastmcp.client.transports

    _FakeFastMcpClient.instances.clear()
    monkeypatch.setattr(fastmcp, "Client", _FakeFastMcpClient)
    monkeypatch.setattr(fastmcp.client.transports, "StdioTransport", _FakeStdioTransport)
    yield


async def test_discovery_registers_correctly_prefixed_tools(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)

    registered = await register_mcp_server("weather.py", prefix="weather", registry=registry)
    assert registered == ["weather__echo"]
    assert registry.get_callable("weather__echo") is not None


async def test_call_through_call_tool_records_normal_tool_call_event(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)
    await register_mcp_server("weather.py", prefix="weather", registry=registry)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        return await agentguard.context.call_tool("weather__echo", query="hi")

    result = await agent("task")
    assert result == "ok"

    run_id = next(iter(fake_repository.runs))
    calls = await fake_repository.list_tool_calls_for_run(run_id)
    assert [(c["tool"], c["outcome"]) for c in calls] == [("weather__echo", "success")]


async def test_iserror_result_produces_failure_outcome(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)
    await register_mcp_server("weather.py", prefix="weather", registry=registry)
    _FakeFastMcpClient.instances[0].responses["echo"] = _FakeCallToolResult(text="boom", is_error=True)

    @monitor(policy=Policy(max_cost=60000, on_tool_exhausted="replan", max_replans=0), llm_judge=False)
    async def agent(task: str) -> str:
        return await agentguard.context.call_tool("weather__echo", query="hi", primary_attempts=1)

    with pytest.raises(Exception):
        await agent("task")

    run_id = next(iter(fake_repository.runs))
    calls = await fake_repository.list_tool_calls_for_run(run_id)
    assert calls[0]["outcome"] == "failure"


async def test_hung_call_past_timeout_produces_failure_or_timeout_outcome(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)
    await register_mcp_server("weather.py", prefix="weather", registry=registry, call_timeout_s=0.05)
    _FakeFastMcpClient.instances[0].hang_on.add("echo")

    @monitor(policy=Policy(max_cost=60000, on_tool_exhausted="replan", max_replans=0), llm_judge=False)
    async def agent(task: str) -> str:
        return await agentguard.context.call_tool("weather__echo", query="hi", primary_attempts=1)

    with pytest.raises(Exception):
        await agent("task")

    run_id = next(iter(fake_repository.runs))
    calls = await fake_repository.list_tool_calls_for_run(run_id)
    assert calls[0]["outcome"] in ("failure", "timeout")


async def test_fallback_still_engages_for_an_mcp_backed_tool(fake_repository):
    registry = ToolRegistry()
    configure_registry(registry)
    await register_mcp_server("weather.py", prefix="weather", registry=registry)
    _FakeFastMcpClient.instances[0].responses["echo"] = _FakeCallToolResult(text="boom", is_error=True)

    async def backup(**kwargs) -> str:
        return "backup result"

    registry.register_tool("weather_backup", backup)
    registry.register_alternative("weather__echo", "weather_backup", reliability_threshold=0.8)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        result = await agentguard.context.call_tool("weather__echo", query="hi", primary_attempts=1)
        agentguard.update_state(max_budget=50000)
        return result

    result = await agent("task")
    assert result == "backup result"

    run_id = next(iter(fake_repository.runs))
    calls = await fake_repository.list_tool_calls_for_run(run_id)
    assert [(c["tool"], c["outcome"], c["is_fallback"]) for c in calls] == [
        ("weather__echo", "failure", False),
        ("weather_backup", "success", True),
    ]
