"""Integration test: a real @monitor-wrapped run automatically captures
git commit/branch/dirty/remote, a dependency lockfile hash, and the SDK's
own version — with zero caller configuration (agentguard/versioning.py,
wired in from agentguard/decorator.py).
"""
from __future__ import annotations

import asyncio

import pytest

import agentguard
from agentguard import Policy, monitor
from agentguard._runtime import reset as reset_repository
from agentguard._version import __version__
from agentguard.human.broker import reset_broker
from agentguard.storage.memory import InMemoryRunRepository
from agentguard.versioning import get_git_metadata, reset_versioning_cache


@pytest.fixture(autouse=True)
def fake_repository():
    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    reset_versioning_cache()
    yield repo
    reset_repository()
    reset_broker()
    reset_versioning_cache()


def test_run_captures_git_and_sdk_version_automatically(fake_repository):
    """This test itself runs from inside the real AGENTGUARD git repo, so
    the captured metadata must match `git` directly -- no mocking."""
    expected = get_git_metadata()
    assert expected is not None, "this test must run inside a real git checkout"

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        return "ok"

    asyncio.run(agent("do something"))

    run_id = next(iter(fake_repository.runs))
    run = fake_repository.runs[run_id]

    assert run.git_commit_sha == expected.commit_sha
    assert len(run.git_commit_sha) == 40
    assert run.git_branch == expected.branch
    assert run.git_dirty == expected.dirty
    assert run.sdk_version == __version__


def test_run_in_non_git_directory_gets_none_fields_not_a_crash(fake_repository, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reset_versioning_cache()

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        return "ok"

    # Must not raise even though tmp_path is not a git repo.
    asyncio.run(agent("do something"))

    run_id = next(iter(fake_repository.runs))
    run = fake_repository.runs[run_id]
    assert run.git_commit_sha is None
    assert run.git_branch is None
    assert run.git_dirty is None
    assert run.sdk_version == __version__  # SDK version is unaffected by git absence
