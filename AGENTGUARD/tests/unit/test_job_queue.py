"""Async job queue: Job model + repository methods (InMemoryRunRepository)
+ Worker (agentguard/jobs/worker.py).

Covers: enqueue/claim/complete/fail lifecycle, retry-until-max-attempts,
an unhandled kind failing immediately (retry=False), a worker with NO
handlers claiming NOTHING (never a cross-kind claim-then-fail), and
Worker.run_once() dispatching to the right handler and marking success/
failure correctly.
"""
from __future__ import annotations

import pytest

import agentguard
from agentguard.jobs import Worker
from agentguard.models import Job

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    yield repo
    reset_repository()


class TestJobLifecycle:
    async def test_enqueue_then_claim_transitions_pending_to_running(self, fake_repository):
        job = Job(kind="demo", payload={"x": 1})
        await fake_repository.enqueue_job(job)

        claimed = await fake_repository.claim_next_job(kinds=["demo"])

        assert claimed["id"] == job.id
        assert claimed["status"] == "running"
        stored = await fake_repository.get_job(job.id)
        assert stored["status"] == "running"

    async def test_claim_ignores_kinds_not_asked_for(self, fake_repository):
        await fake_repository.enqueue_job(Job(kind="other_kind"))
        claimed = await fake_repository.claim_next_job(kinds=["demo"])
        assert claimed is None

    async def test_claim_returns_oldest_pending_first(self, fake_repository):
        import asyncio

        first = Job(kind="demo")
        await fake_repository.enqueue_job(first)
        await asyncio.sleep(0.01)
        second = Job(kind="demo")
        await fake_repository.enqueue_job(second)

        claimed = await fake_repository.claim_next_job(kinds=["demo"])
        assert claimed["id"] == first.id

    async def test_complete_job_marks_it_complete(self, fake_repository):
        job = Job(kind="demo")
        await fake_repository.enqueue_job(job)
        await fake_repository.claim_next_job(kinds=["demo"])
        await fake_repository.complete_job(job.id)
        assert (await fake_repository.get_job(job.id))["status"] == "complete"

    async def test_fail_with_retry_returns_to_pending_until_max_attempts(self, fake_repository):
        job = Job(kind="demo", max_attempts=2)
        await fake_repository.enqueue_job(job)

        await fake_repository.claim_next_job(kinds=["demo"])
        await fake_repository.fail_job(job.id, "boom 1", retry=True)
        after_first = await fake_repository.get_job(job.id)
        assert after_first["status"] == "pending"
        assert after_first["attempts"] == 1

        await fake_repository.claim_next_job(kinds=["demo"])
        await fake_repository.fail_job(job.id, "boom 2", retry=True)
        after_second = await fake_repository.get_job(job.id)
        assert after_second["status"] == "failed"
        assert after_second["attempts"] == 2
        assert after_second["error"] == "boom 2"

    async def test_fail_without_retry_is_terminal_immediately(self, fake_repository):
        job = Job(kind="demo", max_attempts=5)
        await fake_repository.enqueue_job(job)
        await fake_repository.claim_next_job(kinds=["demo"])

        await fake_repository.fail_job(job.id, "no handler", retry=False)

        assert (await fake_repository.get_job(job.id))["status"] == "failed"


class TestWorker:
    async def test_run_once_dispatches_to_the_registered_handler(self, fake_repository):
        seen = []

        async def handler(payload):
            seen.append(payload)

        await fake_repository.enqueue_job(Job(kind="demo", payload={"x": 42}))
        worker = Worker(fake_repository, {"demo": handler})

        claimed = await worker.run_once()

        assert claimed is True
        assert seen == [{"x": 42}]

    async def test_run_once_returns_false_when_queue_is_empty(self, fake_repository):
        worker = Worker(fake_repository, {"demo": lambda payload: None})
        assert await worker.run_once() is False

    async def test_worker_with_no_handlers_never_claims_anything(self, fake_repository):
        # A worker with zero registered handlers must not claim (and
        # thereby permanently fail) jobs meant for a DIFFERENT worker
        # process registering a different handler subset.
        job = Job(kind="demo")
        await fake_repository.enqueue_job(job)
        worker = Worker(fake_repository, {})

        claimed = await worker.run_once()

        assert claimed is False
        assert (await fake_repository.get_job(job.id))["status"] == "pending"

    async def test_a_handler_exception_is_recorded_and_the_job_returns_to_pending(self, fake_repository):
        async def failing_handler(payload):
            raise RuntimeError("handler exploded")

        job = Job(kind="demo", max_attempts=3)
        await fake_repository.enqueue_job(job)
        worker = Worker(fake_repository, {"demo": failing_handler})

        await worker.run_once()

        stored = await fake_repository.get_job(job.id)
        assert stored["status"] == "pending"
        assert stored["attempts"] == 1
        assert "handler exploded" in stored["error"]

    async def test_a_successful_run_marks_the_job_complete(self, fake_repository):
        async def handler(payload):
            pass

        job = Job(kind="demo")
        await fake_repository.enqueue_job(job)
        worker = Worker(fake_repository, {"demo": handler})

        await worker.run_once()

        assert (await fake_repository.get_job(job.id))["status"] == "complete"
