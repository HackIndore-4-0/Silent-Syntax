"""Authentication & tenancy business logic — Dashboard V2.

Every function here operates on the same `RunRepository` the rest of
AgentGuard already writes through (`agentguard._runtime.get_repository()`)
— no separate auth database, no new persistence abstraction. This
module is the ONE place password/session/API-key rules live; the FastAPI
routes (`server/auth_api.py`) and the SDK's `AgentGuard` client
(`agentguard/client.py`) both call into it rather than re-implementing
any of these checks themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import ApiKey, PasswordResetToken, Project, Session, User, Workspace, WorkspaceMembership
from .security import generate_api_key, generate_token, hash_password, hash_token, verify_password


class AuthError(ValueError):
    """Raised for any invalid credential/identity operation. Callers map
    this to 401/403/400 as appropriate — never a stack trace leaked to
    the client."""


@dataclass
class SignupResult:
    user: User
    workspace: Workspace
    project: Project
    session: Session
    raw_session_token: str


async def signup(repository: Any, *, email: str, password: str, name: str) -> SignupResult:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise AuthError("a valid email address is required")
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    if not name.strip():
        raise AuthError("name is required")

    existing = await repository.get_user_by_email(email)
    if existing is not None:
        raise AuthError("an account with this email already exists")

    user = User(email=email, name=name.strip(), password_hash=hash_password(password))
    await repository.save_user(user)

    workspace = Workspace(name=f"{user.name}'s Workspace")
    await repository.save_workspace(workspace)
    await repository.save_workspace_membership(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="owner"))

    project = Project(workspace_id=workspace.id, name="Production")
    await repository.save_project(project)

    session, raw_token = await _create_session(repository, user.id)
    return SignupResult(user=user, workspace=workspace, project=project, session=session, raw_session_token=raw_token)


@dataclass
class LoginResult:
    user: User
    session: Session
    raw_session_token: str


async def login(repository: Any, *, email: str, password: str, user_agent: str = "") -> LoginResult:
    email = email.strip().lower()
    user_data = await repository.get_user_by_email(email)
    if user_data is None:
        raise AuthError("invalid email or password")
    user = User(**user_data)
    if not user.is_active:
        raise AuthError("this account has been deactivated")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid email or password")

    session, raw_token = await _create_session(repository, user.id, user_agent=user_agent)
    return LoginResult(user=user, session=session, raw_session_token=raw_token)


async def _create_session(repository: Any, user_id: str, *, user_agent: str = "") -> tuple[Session, str]:
    raw_token = generate_token()
    session = Session(user_id=user_id, token_hash=hash_token(raw_token), user_agent=user_agent)
    await repository.save_session(session)
    return session, raw_token


async def logout(repository: Any, raw_session_token: str) -> None:
    session_data = await repository.get_session_by_token_hash(hash_token(raw_session_token))
    if session_data is not None:
        await repository.revoke_session(session_data["id"])


@dataclass
class AuthenticatedUser:
    user: User
    session: Session | None
    api_key: ApiKey | None


async def validate_session(repository: Any, raw_session_token: str) -> AuthenticatedUser | None:
    session_data = await repository.get_session_by_token_hash(hash_token(raw_session_token))
    if session_data is None:
        return None
    session = Session(**session_data)
    if not session.is_valid():
        return None
    user_data = await repository.get_user_by_id(session.user_id)
    if user_data is None or not user_data.get("is_active", True):
        return None
    return AuthenticatedUser(user=User(**user_data), session=session, api_key=None)


async def authenticate_api_key(repository: Any, raw_key: str) -> AuthenticatedUser | None:
    key_data = await repository.get_api_key_by_hash(hash_token(raw_key))
    if key_data is None:
        return None
    api_key = ApiKey(**key_data)
    if api_key.status != "active" or api_key.revoked_at is not None:
        return None
    user_data = await repository.get_user_by_id(api_key.user_id)
    if user_data is None or not user_data.get("is_active", True):
        return None
    await repository.update_api_key_last_used(api_key.id)
    return AuthenticatedUser(user=User(**user_data), session=None, api_key=api_key)


async def request_password_reset(repository: Any, email: str) -> str | None:
    """Returns the RAW reset token so a caller (e.g. a dev-mode API
    response, or a real email-sending integration a production
    deployment would add) can deliver it — this environment has no
    outbound email capability, so `POST /api/auth/forgot-password`
    returns it directly in the response instead, with that limitation
    stated plainly (see docs/DASHBOARD_V2_REPORT.md). Returns None
    without creating a token if no account matches `email` — never
    reveals whether an email is registered."""
    user_data = await repository.get_user_by_email(email.strip().lower())
    if user_data is None:
        return None
    raw_token = generate_token()
    token = PasswordResetToken(user_id=user_data["id"], token_hash=hash_token(raw_token))
    await repository.save_password_reset_token(token)
    return raw_token


async def reset_password(repository: Any, *, raw_token: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise AuthError("password must be at least 8 characters")
    token_data = await repository.get_password_reset_token_by_hash(hash_token(raw_token))
    if token_data is None:
        raise AuthError("invalid or expired reset token")
    token = PasswordResetToken(**token_data)
    if not token.is_valid():
        raise AuthError("invalid or expired reset token")

    user_data = await repository.get_user_by_id(token.user_id)
    if user_data is None:
        raise AuthError("invalid or expired reset token")
    user = User(**user_data)
    user.password_hash = hash_password(new_password)
    await repository.save_user(user)
    await repository.mark_password_reset_token_used(token.id)


# -- API keys ------------------------------------------------------------


@dataclass
class CreateApiKeyResult:
    api_key: ApiKey
    raw_key: str


async def create_api_key(
    repository: Any, *, user_id: str, workspace_id: str, project_id: str | None, name: str
) -> CreateApiKeyResult:
    if not name.strip():
        raise AuthError("API key name is required")
    raw_key, key_prefix = generate_api_key()
    api_key = ApiKey(
        user_id=user_id,
        workspace_id=workspace_id,
        project_id=project_id,
        name=name.strip(),
        key_prefix=key_prefix,
        key_hash=hash_token(raw_key),
    )
    await repository.save_api_key(api_key)
    return CreateApiKeyResult(api_key=api_key, raw_key=raw_key)


async def rename_api_key(repository: Any, *, user_id: str, key_id: str, name: str) -> ApiKey:
    key_data = await repository.get_api_key_by_id(key_id)
    if key_data is None or key_data["user_id"] != user_id:
        raise AuthError("API key not found")
    api_key = ApiKey(**key_data)
    api_key.name = name.strip() or api_key.name
    await repository.save_api_key(api_key)
    return api_key


async def revoke_api_key(repository: Any, *, user_id: str, key_id: str) -> ApiKey:
    key_data = await repository.get_api_key_by_id(key_id)
    if key_data is None or key_data["user_id"] != user_id:
        raise AuthError("API key not found")
    api_key = ApiKey(**key_data)
    api_key.status = "revoked"
    api_key.revoked_at = datetime.now(timezone.utc)
    await repository.save_api_key(api_key)
    return api_key


async def rotate_api_key(repository: Any, *, user_id: str, key_id: str) -> CreateApiKeyResult:
    """Revokes the old key and creates a new one with the same name/
    workspace/project — the old raw secret stops working immediately."""
    key_data = await repository.get_api_key_by_id(key_id)
    if key_data is None or key_data["user_id"] != user_id:
        raise AuthError("API key not found")
    old_key = ApiKey(**key_data)
    old_key.status = "revoked"
    old_key.revoked_at = datetime.now(timezone.utc)
    await repository.save_api_key(old_key)

    return await create_api_key(
        repository, user_id=user_id, workspace_id=old_key.workspace_id, project_id=old_key.project_id, name=old_key.name
    )


# -- Workspace / agent resolution ------------------------------------------


async def require_membership(repository: Any, *, workspace_id: str, user_id: str) -> WorkspaceMembership:
    membership_data = await repository.get_membership(workspace_id, user_id)
    if membership_data is None:
        raise AuthError("you do not have access to this workspace")
    return WorkspaceMembership(**membership_data)


async def resolve_project(repository: Any, *, workspace_id: str, project_name: str) -> Project:
    """Finds a project by name within a workspace, creating it if it
    doesn't exist yet (matches the SDK's `AgentGuard(project="production")`
    usage — a project is declared by name, not pre-provisioned by hand)."""
    existing = await repository.list_projects_for_workspace(workspace_id)
    for p in existing:
        if p["name"] == project_name:
            return Project(**p)
    project = Project(workspace_id=workspace_id, name=project_name)
    await repository.save_project(project)
    return project


async def resolve_agent(repository: Any, *, workspace_id: str, project_id: str | None, agent_name: str):
    """Auto-registers an AgentRegistration on first use within a
    workspace — this is the stable `agent_id` Behavior Fingerprint / Tool
    pages key their per-agent views on."""
    from .models import AgentRegistration

    existing = await repository.get_agent_registration(workspace_id, agent_name)
    if existing is not None:
        return AgentRegistration(**existing)
    agent = AgentRegistration(workspace_id=workspace_id, project_id=project_id, name=agent_name)
    await repository.save_agent_registration(agent)
    return agent
