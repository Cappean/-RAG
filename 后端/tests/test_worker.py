import asyncio

import pytest

from medical_consultation_backend.core.config import Settings
from medical_consultation_backend.tasks.handlers import HANDLERS
from medical_consultation_backend.tasks.models import Job
from medical_consultation_backend.tasks.worker import Worker


class MemoryRepository:
    def __init__(self) -> None:
        self.job = Job(kind="demo.noop")
        self.completed: dict | None = None
        self.failures: list[bool] = []

    async def claim(self, lease_seconds: float):
        if self.job.status != "queued":
            return None
        self.job.status = "running"
        self.job.attempts += 1
        self.job.lease_token = "test"
        return self.job

    async def heartbeat(self, job, lease_seconds):
        return True

    async def complete(self, job, result):
        self.job.status = "succeeded"
        self.completed = result
        return True

    async def fail(self, job, permanent=False):
        self.failures.append(permanent)
        self.job.status = "failed" if permanent or job.attempts >= job.max_attempts else "queued"
        return True


@pytest.mark.asyncio
async def test_worker_executes_handler_once() -> None:
    repository = MemoryRepository()
    worker = Worker(repository, HANDLERS, Settings(worker_heartbeat_seconds=1))
    assert await worker.run_once() is True
    assert repository.job.status == "succeeded"
    assert repository.completed == {"processed": True, "jobId": repository.job.id}


async def test_unknown_handler_fails_without_retry() -> None:
    repository = MemoryRepository()
    repository.job.kind = "not.registered"
    worker = Worker(repository, HANDLERS, Settings())
    await worker.run_once()
    assert repository.job.status == "failed"
    assert repository.failures == [True]


async def test_transient_failure_retries_until_limit() -> None:
    repository = MemoryRepository()
    repository.job.max_attempts = 2

    async def failing(job):
        raise RuntimeError("temporary")

    worker = Worker(repository, {"demo.noop": failing}, Settings())
    await worker.run_once()
    assert repository.job.status == "queued"
    await worker.run_once()
    assert repository.job.status == "failed"
    assert repository.job.attempts == 2
    assert repository.completed is None


async def test_lease_loss_cancels_execution_without_acknowledging() -> None:
    repository = MemoryRepository()
    cancelled = asyncio.Event()

    async def slow(job):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def lost_lease(job, lease_seconds):
        return False

    repository.heartbeat = lost_lease
    worker = Worker(repository, {"demo.noop": slow}, Settings(worker_heartbeat_seconds=0.005))
    await asyncio.wait_for(worker.run_once(), timeout=1)
    assert cancelled.is_set()
    assert repository.completed is None
    assert repository.failures == []


async def test_cancelled_worker_leaves_job_for_lease_recovery() -> None:
    repository = MemoryRepository()
    started = asyncio.Event()

    async def slow(job):
        started.set()
        await asyncio.Event().wait()

    worker = Worker(repository, {"demo.noop": slow}, Settings())
    task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repository.job.status == "running"
    assert repository.completed is None
    assert repository.failures == []


async def test_worker_shutdown_stops_polling() -> None:
    repository = MemoryRepository()
    repository.job.status = "succeeded"
    worker = Worker(repository, HANDLERS, Settings(worker_poll_seconds=0.005))
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    stop.set()
    await asyncio.wait_for(task, timeout=1)
