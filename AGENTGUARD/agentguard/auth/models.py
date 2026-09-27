"""Multi-user authentication & tenancy domain models — Dashboard V2.

Kept separate from `agentguard.models` (the execution-domain models:
Run, Decision, Checkpoint, ...) the same way `agentguard.storage.models`
is kept separate from it — this module describes WHO owns data, not
what AgentGuard observed. A `Run` (and every other top-level, non-run-
scoped resource: `PolicyDefinition`, `ToolAlternative`,
`ImprovementCandidate`) carries a `workspace_id` pointing back here;
everything else (checkpoints, audit events, tool calls, decisions,
evaluations) inherits its isolation boundary transitively through its
`run_id` foreign key, so isolation is enforced once per resource type,
not re-threaded through every child table.

Passwords are never stored in plaintext (PBKDF2-HMAC-SHA256, see
`agentguard/auth/security.py`). Session tokens and API keys are stored
only as a SHA-256 hash of the secret actually handed to the client —
the raw secret exists only in the HTTP response that created it and is
never persisted or logged anywhere.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class User(BaseModel):
    id: str = Field(default_factory=_new_id)
    email: str
    name: str
    password_hash: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=_now)

    def public(self) -> dict:
        """Never include password_hash in anything sent to a client."""
        return {"id": self.id, "email": self.email, "name": self.name, "created_at": self.created_at.isoformat()}


class Session(BaseModel):
    id: str = Field(default_factory=_new_id)
    user_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(default_factory=lambda: _now() + timedelta(days=14))
    revoked_at: datetime | None = None
    user_agent: str = ""

    def is_valid(self, *, at: datetime | None = None) -> bool:
        now = at or _now()
        return self.revoked_at is None and self.expires_at > now


class PasswordResetToken(BaseModel):
    id: str = Field(default_factory=_new_id)
    user_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(default_factory=lambda: _now() + timedelta(hours=1))
    used_at: datetime | None = None

    def is_valid(self, *, at: datetime | None = None) -> bool:
        now = at or _now()
        return self.used_at is None and self.expires_at > now


class Workspace(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    created_at: datetime = Field(default_factory=_now)


WorkspaceRole = Literal["owner", "member"]


class WorkspaceMembership(BaseModel):
    workspace_id: str
    user_id: str
    role: WorkspaceRole = "member"
    created_at: datetime = Field(default_factory=_now)


class Project(BaseModel):
    id: str = Field(default_factory=_new_id)
    workspace_id: str
    name: str
    created_at: datetime = Field(default_factory=_now)


class AgentRegistration(BaseModel):
    """One row per distinct agent_name ever seen within a workspace —
    what Agent Behavior / Tool pages key their per-agent views on."""

    id: str = Field(default_factory=_new_id)
    workspace_id: str
    project_id: str | None = None
    name: str
    created_at: datetime = Field(default_factory=_now)
    description: str | None = None
    """Author-supplied only (agentguard/a2a/card.py's update_agent_card())
    — never derived/guessed. None until an author explicitly sets it."""
    capabilities: list[str] = Field(default_factory=list)
    """Author-supplied only, same rule as description — AgentGuard has
    no way to derive an agent's actual capabilities from its runs, so
    this is empty unless explicitly declared."""


ApiKeyStatus = Literal["active", "revoked"]


class ApiKey(BaseModel):
    id: str = Field(default_factory=_new_id)
    user_id: str
    workspace_id: str
    project_id: str | None = None
    name: str
    key_prefix: str
    """Safe to display forever, e.g. "agp_live_ab12cd34" — never enough
    to reconstruct the secret."""
    key_hash: str
    """SHA-256 of the full raw key. The raw key itself is never stored."""
    created_at: datetime = Field(default_factory=_now)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    status: ApiKeyStatus = "active"

    def public(self) -> dict:
        """Never include key_hash — the whole point of hashing it."""
        return {
            "id": self.id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "status": self.status,
        }
