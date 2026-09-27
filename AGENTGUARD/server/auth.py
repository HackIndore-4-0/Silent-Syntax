"""Dashboard V2 — authentication routes and the auth dependency every
workspace-scoped endpoint in `server/dashboard_v2.py` uses.

Two credential types are accepted (Rule: "Authorization: Bearer
<API_KEY> or equivalent SDK authentication mechanism"):
    - a session cookie (`agentguard_session`, httponly) — the dashboard
      frontend, set on signup/login.
    - `Authorization: Bearer <api_key>` — the SDK / programmatic access.

Both resolve to the same `AuthenticatedUser` shape; every downstream
handler only ever sees "this request is user X, workspace Y" and never
has to care which credential type produced it.
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Header, Request, Response
from pydantic import BaseModel

from agentguard._runtime import get_repository
from agentguard.auth import AuthError
from agentguard.auth.service import (
    AuthenticatedUser,
    authenticate_api_key,
    create_api_key,
    login as auth_login,
    logout as auth_logout,
    rename_api_key,
    request_password_reset,
    reset_password as auth_reset_password,
    revoke_api_key,
    rotate_api_key,
    signup as auth_signup,
    validate_session,
)

router = APIRouter()

SESSION_COOKIE = "agentguard_session"


async def get_current_user(
    request: Request,
    agentguard_session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    """The auth dependency. Raises 401 for anything invalid — an
    expired/revoked session, an unknown/revoked API key, or no
    credential at all. Never falls back to "no filter" (that would be
    exactly the frontend-only-isolation anti-pattern the spec forbids)."""
    repository = get_repository()

    if authorization and authorization.lower().startswith("bearer "):
        raw_key = authorization[7:].strip()
        auth_user = await authenticate_api_key(repository, raw_key)
        if auth_user is None:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
        return auth_user

    if agentguard_session:
        auth_user = await validate_session(repository, agentguard_session)
        if auth_user is None:
            raise HTTPException(status_code=401, detail="session expired or invalid — please log in again")
        return auth_user

    raise HTTPException(status_code=401, detail="authentication required")


async def get_current_workspace_id(auth_user: AuthenticatedUser = Depends(get_current_user)) -> str:
    """Every authenticated request resolves to exactly one workspace —
    the one the credential (session's user, or API key) was
    provisioned for. This is the value every `workspace_id=` repository
    filter in server/dashboard_v2.py is populated from — the actual
    enforcement point, not a frontend display choice."""
    if auth_user.api_key is not None:
        return auth_user.api_key.workspace_id
    repository = get_repository()
    memberships = await repository.list_memberships_for_user(auth_user.user.id)
    if not memberships:
        raise HTTPException(status_code=403, detail="this account has no workspace")
    return memberships[0]["workspace_id"]


# -- signup / login / logout / password reset / profile ---------------------


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        httponly=True,
        samesite="lax",
        max_age=14 * 24 * 3600,
        path="/",
    )


@router.post("/api/auth/signup")
async def signup_route(body: SignupRequest, response: Response):
    repository = get_repository()
    try:
        result = await auth_signup(repository, email=body.email, password=body.password, name=body.name)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _set_session_cookie(response, result.raw_session_token)
    return {
        "user": result.user.public(),
        "workspace": {"id": result.workspace.id, "name": result.workspace.name},
        "project": {"id": result.project.id, "name": result.project.name},
    }


@router.post("/api/auth/login")
async def login_route(body: LoginRequest, response: Response, request: Request):
    repository = get_repository()
    try:
        result = await auth_login(
            repository, email=body.email, password=body.password, user_agent=request.headers.get("user-agent", "")
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    _set_session_cookie(response, result.raw_session_token)
    return {"user": result.user.public()}


@router.post("/api/auth/logout")
async def logout_route(response: Response, agentguard_session: str | None = Cookie(default=None)):
    if agentguard_session:
        repository = get_repository()
        await auth_logout(repository, agentguard_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"logged_out": True}


class ForgotPasswordRequest(BaseModel):
    email: str


@router.post("/api/auth/forgot-password")
async def forgot_password_route(body: ForgotPasswordRequest):
    """LIMITATION (stated, not concealed — see docs/DASHBOARD_V2_REPORT.md):
    this environment has no outbound email capability, so the reset
    token is returned directly in the response instead of being emailed.
    A production deployment would email it and never return it here."""
    repository = get_repository()
    raw_token = await request_password_reset(repository, body.email)
    return {
        "detail": "If an account exists for this email, a reset token has been issued.",
        "dev_reset_token": raw_token,
    }


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/api/auth/reset-password")
async def reset_password_route(body: ResetPasswordRequest):
    repository = get_repository()
    try:
        await auth_reset_password(repository, raw_token=body.token, new_password=body.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"reset": True}


@router.get("/api/auth/me")
async def me_route(auth_user: AuthenticatedUser = Depends(get_current_user)):
    repository = get_repository()
    memberships = await repository.list_memberships_for_user(auth_user.user.id)
    workspaces = []
    for m in memberships:
        w = await repository.get_workspace(m["workspace_id"])
        if w:
            workspaces.append({"id": w["id"], "name": w["name"], "role": m["role"]})
    return {"user": auth_user.user.public(), "workspaces": workspaces}


# -- API keys -----------------------------------------------------------------


class CreateApiKeyRequest(BaseModel):
    name: str
    project_id: str | None = None


@router.get("/api/settings/api-keys")
async def list_api_keys_route(auth_user: AuthenticatedUser = Depends(get_current_user)):
    repository = get_repository()
    keys = await repository.list_api_keys_for_user(auth_user.user.id)
    from agentguard.auth.models import ApiKey

    return [ApiKey(**k).public() for k in keys]


@router.post("/api/settings/api-keys")
async def create_api_key_route(
    body: CreateApiKeyRequest,
    auth_user: AuthenticatedUser = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
):
    repository = get_repository()
    try:
        result = await create_api_key(
            repository, user_id=auth_user.user.id, workspace_id=workspace_id, project_id=body.project_id, name=body.name
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"api_key": result.api_key.public(), "raw_key": result.raw_key}


class RenameApiKeyRequest(BaseModel):
    name: str


@router.patch("/api/settings/api-keys/{key_id}")
async def rename_api_key_route(key_id: str, body: RenameApiKeyRequest, auth_user: AuthenticatedUser = Depends(get_current_user)):
    repository = get_repository()
    try:
        updated = await rename_api_key(repository, user_id=auth_user.user.id, key_id=key_id, name=body.name)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return updated.public()


@router.post("/api/settings/api-keys/{key_id}/revoke")
async def revoke_api_key_route(key_id: str, auth_user: AuthenticatedUser = Depends(get_current_user)):
    repository = get_repository()
    try:
        updated = await revoke_api_key(repository, user_id=auth_user.user.id, key_id=key_id)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return updated.public()


@router.post("/api/settings/api-keys/{key_id}/rotate")
async def rotate_api_key_route(key_id: str, auth_user: AuthenticatedUser = Depends(get_current_user)):
    repository = get_repository()
    try:
        result = await rotate_api_key(repository, user_id=auth_user.user.id, key_id=key_id)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"api_key": result.api_key.public(), "raw_key": result.raw_key}


# -- projects / team ------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    name: str


@router.get("/api/projects")
async def list_projects_route(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    return await repository.list_projects_for_workspace(workspace_id)


@router.post("/api/projects")
async def create_project_route(body: CreateProjectRequest, workspace_id: str = Depends(get_current_workspace_id)):
    from agentguard.auth.models import Project

    repository = get_repository()
    project = Project(workspace_id=workspace_id, name=body.name)
    await repository.save_project(project)
    return project.model_dump()


@router.get("/api/workspace/members")
async def list_workspace_members_route(workspace_id: str = Depends(get_current_workspace_id)):
    repository = get_repository()
    memberships = await repository.list_memberships_for_workspace(workspace_id)
    result = []
    for m in memberships:
        user = await repository.get_user_by_id(m["user_id"])
        if user:
            result.append({"user_id": user["id"], "email": user["email"], "name": user["name"], "role": m["role"]})
    return result


class InviteMemberRequest(BaseModel):
    email: str
    role: str = "member"


@router.post("/api/workspace/members")
async def invite_member_route(
    body: InviteMemberRequest,
    workspace_id: str = Depends(get_current_workspace_id),
    auth_user: AuthenticatedUser = Depends(get_current_user),
):
    """LIMITATION (stated, not concealed): this environment cannot send
    an invitation email, so "invite" only works for an email that
    already has an AgentGuard account — it adds that existing user to
    the workspace directly. A production deployment would email a
    real invite link to a not-yet-registered address instead."""
    from agentguard.auth.models import WorkspaceMembership

    repository = get_repository()
    invitee = await repository.get_user_by_email(body.email)
    if invitee is None:
        raise HTTPException(
            status_code=404,
            detail="no AgentGuard account found for this email — invitation email delivery is not available in this environment",
        )
    membership = WorkspaceMembership(workspace_id=workspace_id, user_id=invitee["id"], role=body.role)
    await repository.save_workspace_membership(membership)
    return {"user_id": invitee["id"], "email": invitee["email"], "role": membership.role}
