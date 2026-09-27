"""API key unit tests — Dashboard V2 (spec §20).

Covers: create, authenticate SDK, revoke, rotate, invalid key rejected.
"""
from __future__ import annotations

import pytest

from agentguard.auth import AuthError, authenticate_api_key, create_api_key, revoke_api_key, rotate_api_key, signup

from ..fakes import InMemoryRunRepository


@pytest.fixture
def repo():
    return InMemoryRunRepository()


async def _signed_up_user(repo):
    return await signup(repo, email="alice@example.com", password="hunter22", name="Alice")


async def test_create_api_key_returns_raw_key_once_and_never_stores_it(repo):
    signup_result = await _signed_up_user(repo)
    result = await create_api_key(
        repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id, project_id=signup_result.project.id, name="CI key"
    )
    assert result.raw_key.startswith("agp_live_")
    assert result.api_key.key_prefix == result.raw_key[:16]

    stored = await repo.get_api_key_by_id(result.api_key.id)
    assert stored is not None
    assert "key_hash" in stored
    assert stored["key_hash"] != result.raw_key  # never the raw secret
    assert result.raw_key not in str(stored)  # raw key genuinely absent from the stored record


async def test_authenticate_api_key_succeeds_for_a_valid_key(repo):
    signup_result = await _signed_up_user(repo)
    result = await create_api_key(
        repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id, project_id=signup_result.project.id, name="CI key"
    )
    auth_user = await authenticate_api_key(repo, result.raw_key)
    assert auth_user is not None
    assert auth_user.user.id == signup_result.user.id
    assert auth_user.api_key.id == result.api_key.id


async def test_authenticate_rejects_invalid_key(repo):
    auth_user = await authenticate_api_key(repo, "agp_live_totally_made_up")
    assert auth_user is None


async def test_authenticate_rejects_revoked_key(repo):
    signup_result = await _signed_up_user(repo)
    result = await create_api_key(
        repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id, project_id=signup_result.project.id, name="CI key"
    )
    await revoke_api_key(repo, user_id=signup_result.user.id, key_id=result.api_key.id)

    auth_user = await authenticate_api_key(repo, result.raw_key)
    assert auth_user is None


async def test_revoke_updates_status_and_timestamp(repo):
    signup_result = await _signed_up_user(repo)
    result = await create_api_key(
        repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id, project_id=signup_result.project.id, name="CI key"
    )
    revoked = await revoke_api_key(repo, user_id=signup_result.user.id, key_id=result.api_key.id)
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None


async def test_revoke_someone_elses_key_raises(repo):
    alice = await _signed_up_user(repo)
    bob = await signup(repo, email="bob@example.com", password="hunter22", name="Bob")
    alice_key = await create_api_key(
        repo, user_id=alice.user.id, workspace_id=alice.workspace.id, project_id=alice.project.id, name="alice key"
    )

    with pytest.raises(AuthError, match="not found"):
        await revoke_api_key(repo, user_id=bob.user.id, key_id=alice_key.api_key.id)


async def test_rotate_invalidates_old_key_and_issues_a_new_one(repo):
    signup_result = await _signed_up_user(repo)
    original = await create_api_key(
        repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id, project_id=signup_result.project.id, name="CI key"
    )
    rotated = await rotate_api_key(repo, user_id=signup_result.user.id, key_id=original.api_key.id)

    assert rotated.raw_key != original.raw_key
    assert rotated.api_key.id != original.api_key.id
    assert rotated.api_key.name == original.api_key.name  # preserved

    old_auth = await authenticate_api_key(repo, original.raw_key)
    assert old_auth is None  # old key stopped working immediately

    new_auth = await authenticate_api_key(repo, rotated.raw_key)
    assert new_auth is not None
    assert new_auth.user.id == signup_result.user.id


async def test_create_api_key_requires_a_name(repo):
    signup_result = await _signed_up_user(repo)
    with pytest.raises(AuthError, match="name is required"):
        await create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id, project_id=signup_result.project.id, name="   "
        )
