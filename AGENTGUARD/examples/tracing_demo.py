"""Runnable proof of TraceStep tracing (@traceable / wrap_llm_client).
Requires a reachable PostgreSQL instance (see README.md).

    python -m agentguard.cli init-db      # once, to create the schema
    python examples/tracing_demo.py

Then query agentguard_trace_steps / agentguard_audit_events for the
printed run_id to see the nested tree and hash-chained audit trail.
"""
from __future__ import annotations

import asyncio

from agentguard import Policy, monitor, traceable


@traceable
async def fetch_context(query: str) -> str:
    """A traced helper step — its full input/output get recorded."""
    return f"context for {query!r}"


@traceable
async def summarize(text: str) -> str:
    """Nested under research_agent's own trace: calling this from inside
    research_agent automatically sets its parent_step_id."""
    return f"summary of: {text}"


@monitor(policy=Policy(max_cost=60000))
async def research_agent(task: str) -> str:
    context = await fetch_context(task)
    return await summarize(context)


async def main() -> None:
    result = await research_agent("find a laptop under budget")
    print(f"[result] {result}")


if __name__ == "__main__":
    asyncio.run(main())
