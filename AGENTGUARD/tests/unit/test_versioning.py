from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentguard.versioning import (
    get_dependency_snapshot,
    get_git_metadata,
    reset_versioning_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_versioning_cache()
    yield
    reset_versioning_cache()


def test_get_git_metadata_against_this_real_repo():
    """This test file itself lives inside a real git repo -- exercise the
    function against real `git` rather than a mock, so a genuinely broken
    subprocess invocation (wrong flag, wrong cwd handling) is caught."""
    metadata = get_git_metadata(cwd=str(Path(__file__).resolve().parent))
    assert metadata is not None
    assert metadata.commit_sha is not None and len(metadata.commit_sha) == 40
    assert metadata.branch is not None
    assert metadata.dirty in (True, False)  # never None when commit_sha resolved


def test_get_git_metadata_returns_none_outside_a_git_repo(tmp_path):
    metadata = get_git_metadata(cwd=str(tmp_path))
    assert metadata is None


def test_get_git_metadata_is_cached_per_cwd(tmp_path, monkeypatch):
    calls = {"count": 0}
    real_run = subprocess.run

    def _counting_run(*args, **kwargs):
        calls["count"] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _counting_run)

    repo_dir = str(Path(__file__).resolve().parent)
    first = get_git_metadata(cwd=repo_dir)
    calls_after_first = calls["count"]
    second = get_git_metadata(cwd=repo_dir)

    assert calls_after_first > 0
    assert calls["count"] == calls_after_first  # second call made zero new subprocess calls
    assert first == second


def test_get_git_metadata_detects_dirty_working_tree(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("hello")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)

    clean = get_git_metadata(cwd=str(tmp_path))
    assert clean is not None
    assert clean.dirty is False

    reset_versioning_cache()
    (tmp_path / "a.txt").write_text("changed")
    dirty = get_git_metadata(cwd=str(tmp_path))
    assert dirty is not None
    assert dirty.dirty is True


def test_get_dependency_snapshot_finds_uv_lock(tmp_path):
    (tmp_path / "uv.lock").write_text("some lockfile content")
    snapshot = get_dependency_snapshot(cwd=str(tmp_path))
    assert snapshot is not None
    assert snapshot.lockfile_path == "uv.lock"
    assert len(snapshot.lockfile_hash) == 64  # sha256 hex digest


def test_get_dependency_snapshot_returns_none_when_no_lockfile_present(tmp_path):
    assert get_dependency_snapshot(cwd=str(tmp_path)) is None


def test_get_dependency_snapshot_hash_changes_with_content(tmp_path):
    lockfile = tmp_path / "uv.lock"
    lockfile.write_text("v1")
    first = get_dependency_snapshot(cwd=str(tmp_path))
    reset_versioning_cache()
    lockfile.write_text("v2")
    second = get_dependency_snapshot(cwd=str(tmp_path))
    assert first.lockfile_hash != second.lockfile_hash
