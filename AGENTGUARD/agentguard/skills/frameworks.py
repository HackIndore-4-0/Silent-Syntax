"""Framework template library for the Skills Generator. Each body is
plain Markdown containing literal `__PROJECT_NAME__` tokens — replaced
via a single `.replace()` pass in `render.py`, never `.format()` or an
f-string, so the Python code samples' own `{`/`}` braces are never
mistaken for template placeholders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FrameworkKey = Literal["plain_python", "langgraph", "generic"]


@dataclass(frozen=True)
class FrameworkTemplate:
    key: str
    label: str
    description: str
    step_title: str
    body: str
    verify: str


_PLAIN_PYTHON_BODY = """Find the function in this repository that receives a task/prompt and
returns the agent's final answer — usually a single async or sync
function that calls an LLM. Wrap it with `@monitor`:

    import os
    from agentguard import AgentGuard, Policy

    guard = AgentGuard(api_key=os.environ["AGENTGUARD_API_KEY"], project="__PROJECT_NAME__")

    @guard.monitor(policy=Policy(max_cost=60000))
    async def your_agent_entry_point(task: str) -> str:
        ...  # existing logic, unchanged
        return result
"""

_LANGGRAPH_BODY = """This is a LangGraph `StateGraph`-based agent. Wrap the function that
calls `graph.ainvoke(...)` (the entry point) with `@monitor`, and wrap
each individual graph node function (a tool call, an LLM call, a
retrieval step) with `@traceable` so it shows up as its own step in the
dashboard's Trace/Steps view:

    import os
    from agentguard import AgentGuard, Policy
    from agentguard.tracing import traceable

    guard = AgentGuard(api_key=os.environ["AGENTGUARD_API_KEY"], project="__PROJECT_NAME__")

    @traceable
    async def your_graph_node(state: dict) -> dict:
        ...  # existing node logic, unchanged
        return state

    @guard.monitor(policy=Policy(max_cost=60000))
    async def your_agent_entry_point(task: str) -> str:
        result = await your_graph.ainvoke({"query": task})
        return result
"""

_GENERIC_BODY = """No framework was specified. Before writing any code:

1. Inspect this repository's structure (README, package manifest, entry
   scripts) to detect the language, package manager, and agent framework
   in use (LangGraph, LangChain, a plain function, a custom loop, etc.).
2. Find the function that receives a task/prompt and returns the agent's
   final answer.
3. Install AgentGuard for this project's package manager (Python only
   today — if this project isn't Python, stop and report that).
4. Wrap that entry point with `@monitor`:

    import os
    from agentguard import AgentGuard, Policy

    guard = AgentGuard(api_key=os.environ["AGENTGUARD_API_KEY"], project="__PROJECT_NAME__")

    @guard.monitor(policy=Policy(max_cost=60000))
    async def your_agent_entry_point(task: str) -> str:
        ...  # existing logic, unchanged
        return result
"""

FRAMEWORK_TEMPLATES: dict[str, FrameworkTemplate] = {
    "plain_python": FrameworkTemplate(
        key="plain_python", label="Plain Python function",
        description="A single async/sync function that calls an LLM directly, no framework.",
        step_title="Wrap your agent's entry point",
        body=_PLAIN_PYTHON_BODY,
        verify="Run the agent once. Check __API_BASE_URL__/#/runs — a new Run should appear with status \"continue\" or \"stop\", not an unhandled exception.",
    ),
    "langgraph": FrameworkTemplate(
        key="langgraph", label="LangGraph agent",
        description="A StateGraph-based agent with multiple graph nodes.",
        step_title="Wrap your agent's entry point and its internal steps",
        body=_LANGGRAPH_BODY,
        verify="Run the agent once. Check __API_BASE_URL__/#/runs for the new Run, then open it and confirm the Trace/Steps tab shows one step per @traceable-wrapped node, not just the single entry-point call.",
    ),
    "generic": FrameworkTemplate(
        key="generic", label="Detect automatically",
        description="No framework assumed — instructs the coding agent to inspect the repo first.",
        step_title="Find the entry point and wrap it",
        body=_GENERIC_BODY,
        verify="Run the agent once. Check __API_BASE_URL__/#/runs — a new Run should appear for this project.",
    ),
}
