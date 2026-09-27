"""MCP (Model Context Protocol) interoperability.

Two independent, optional capabilities, both lazily importing the `mcp`
package (pip install agentguard[mcp]) so `import agentguard` never
requires it:

- `register_mcp_server()` (client.py): registers an external MCP
  server's tools into agentguard.tools.ToolRegistry, so they get the
  exact same ToolCallEvent/reliability-profiling/fallback/audit-trail
  instrumentation as any other agentguard.call_tool()-invoked tool.
- `agentguard/mcp/server.py`: a minimal, read-only MCP server exposing
  this workspace's own governance data (run history, reliability
  reports, audit verification, tool reliability) for an external MCP
  host to query — see `agentguard mcp-serve`.
"""
from .client import MCPToolError, close_mcp_server, register_mcp_server, reset_mcp_connections

__all__ = ["register_mcp_server", "close_mcp_server", "reset_mcp_connections", "MCPToolError"]
