"""A2A Agent Card — a small, honest identity manifest.

AgentGuard only genuinely knows an agent's name, workspace/project, and
(via its runs) its latest agent_version — no "skills" list, no
invocable endpoint. Per the "no fabricated data" principle already
enforced everywhere else in this codebase, `capabilities` stays empty
and `url` stays None unless an author explicitly declares them via
`agentguard.storage.repository.RunRepository.update_agent_card()`.

Served at GET /api/v2/agents/{agent_name}/card (server/dashboard_v2.py)
— NOT A2A's conventional public `/.well-known/agent.json` path, which
assumes unauthenticated cross-organization discovery and directly
conflicts with AgentGuard's workspace-tenancy model. A genuinely public
card is separate future work requiring an explicit opt-in flag.
"""
from __future__ import annotations

from typing import Any

from ..models import AgentCard


def build_agent_card(agent: dict[str, Any], *, latest_agent_version: str | None) -> AgentCard:
    capabilities = agent.get("capabilities") or []
    return AgentCard(
        id=agent["id"],
        name=agent["name"],
        description=agent.get("description"),
        version=latest_agent_version,
        capabilities=list(capabilities),
        capabilities_declared_by_author=bool(capabilities),
        workspace_id=agent["workspace_id"],
    )
