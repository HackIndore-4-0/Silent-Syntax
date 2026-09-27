"""Authentication unit tests — Dashboard V2 (spec §20).

Covers: signup, login, logout, invalid credentials, session expiration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentguard.auth import AuthError, login, logout, signup, validate_session
from agentguard.auth.models import Session
from agentguard.auth.security import hash_password, hash_token, verify_password

from ..fakes import InMemoryRunRepository


@pytest.fixture
def repo():
    return InMemoryRunRepository()


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_password_hash_is_salted_differently_each_time():
    a = hash_password("same password")
    b = hash_password("same password")
    assert a != b  # different salts
    assert verify_password("same password", a)
    assert verify_password("same password", b)


async def test_signup_creates_user_workspace_and_project(repo):
    result = await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    assert result.user.email == "alice@example.com"
    assert result.workspace.name == "Alice's Workspace"
    assert result.project.name == "Production"
    assert result.raw_session_token

    stored = await repo.get_user_by_email("alice@example.com")
    assert stored is not None
    assert stored["password_hash"] != "hunter22"  # never plaintext


async def test_signup_rejects_duplicate_email(repo):
    await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    with pytest.raises(AuthError, match="already exists"):
        await signup(repo, email="ALICE@example.com", password="different1", name="Alice2")  # case-insensitive


async def test_signup_rejects_short_password(repo):
    with pytest.raises(AuthError, match="8 characters"):
        await signup(repo, email="alice@example.com", password="short", name="Alice")


async def test_login_succeeds_with_correct_credentials(repo):
    await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    result = await login(repo, email="alice@example.com", password="hunter22")
    assert result.user.email == "alice@example.com"
    assert result.raw_session_token


async def test_login_rejects_invalid_credentials(repo):
    await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    with pytest.raises(AuthError, match="invalid email or password"):
        await login(repo, email="alice@example.com", password="wrong-password")


async def test_login_rejects_unknown_email(repo):
    with pytest.raises(AuthError, match="invalid email or password"):
        await login(repo, email="nobody@example.com", password="whatever1")


async def test_login_never_reveals_whether_email_exists(repo):
    """Both 'wrong password' and 'unknown email' produce the identical
    error message — never leak which case it was."""
    await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    try:
        await login(repo, email="alice@example.com", password="wrong-password")
        assert False
    except AuthError as e1:
        msg1 = str(e1)
    try:
        await login(repo, email="nobody@example.com", password="whatever1")
        assert False
    except AuthError as e2:
        msg2 = str(e2)
    assert msg1 == msg2


async def test_logout_revokes_the_session(repo):
    signup_result = await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    token = signup_result.raw_session_token

    auth_user = await validate_session(repo, token)
    assert auth_user is not None

    await logout(repo, token)
    auth_user_after = await validate_session(repo, token)
    assert auth_user_after is None


async def test_validate_session_rejects_unknown_token(repo):
    result = await validate_session(repo, "not-a-real-token")
    assert result is None


async def test_session_expiration_is_enforced(repo):
    signup_result = await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    # Directly age the session past its expiry, simulating time passing.
    session_data = await repo.get_session_by_token_hash(hash_token(signup_result.raw_session_token))
    session = Session(**session_data)
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await repo.save_session(session)

    auth_user = await validate_session(repo, signup_result.raw_session_token)
    assert auth_user is None


async def test_deactivated_user_cannot_log_in(repo):
    result = await signup(repo, email="alice@example.com", password="hunter22", name="Alice")
    user_data = await repo.get_user_by_id(result.user.id)
    from agentguard.auth.models import User

    user = User(**user_data)
    user.is_active = False
    await repo.save_user(user)

    with pytest.raises(AuthError, match="deactivated"):
        await login(repo, email="alice@example.com", password="hunter22")
