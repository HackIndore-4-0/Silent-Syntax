"""`agentguard worker` CLI handler wiring (agentguard/cli/__init__.py::_worker).

Covers only that the evaluation_suite_run job kind is registered
unconditionally (needs just the repository, unlike dataset_validation's
external evidence-source env-var gate) -- not the worker's polling loop
itself, which is already covered by agentguard/jobs/worker.py's own tests.
"""
from __future__ import annotations

import agentguard
import pytest
from agentguard.jobs import EVALUATION_SUITE_RUN_JOB_KIND, Worker

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


async def test_evaluation_suite_run_handler_is_registered_unconditionally(monkeypatch):
    from agentguard.cli import _worker

    captured: dict = {}

    async def fake_run_forever(self, *, poll_interval_s):
        captured["handlers"] = dict(self._handlers)

    monkeypatch.setattr(Worker, "run_forever", fake_run_forever)

    await _worker(2.0)

    assert EVALUATION_SUITE_RUN_JOB_KIND in captured["handlers"]
