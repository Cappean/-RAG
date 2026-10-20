"""Exercise accepted queue design, replacing retired Mongo lease/retry tests."""

import asyncio

import pytest

from app.core.errors import AppError
from app.worker.handlers import HANDLERS
from app.worker.queue import MemoryTaskQueue
from app.worker.runner import Worker


async def test_worker_executes_handler_once() -> None:
    queue = MemoryTaskQueue()
    record = queue.submit("demo.noop")
    await Worker(queue, HANDLERS).run_once()
    assert record.status == "succeeded"
    assert record.result == {"processed": True, "taskId": record.id}


async def test_failure_isolation_and_no_retry_or_sensitive_error() -> None:
    queue = MemoryTaskQueue()

    async def failing(record):
        raise RuntimeError("sensitive clinical text")

    first = queue.submit("failure")
    second = queue.submit("demo.noop")
    worker = Worker(queue, {**HANDLERS, "failure": failing})
    await worker.run_once()
    await worker.run_once()
    assert first.status == "failed"
    assert "sensitive" not in first.error
    assert second.status == "succeeded"


async def test_queue_rejects_overflow_without_registering_new_record() -> None:
    queue = MemoryTaskQueue(capacity=1)
    record = queue.submit("demo.noop")
    with pytest.raises(AppError, match="队列已满"):
        queue.submit("demo.noop")
    assert queue.get(record.id) is record


async def test_completed_retention_does_not_evict_unfinished_tasks() -> None:
    queue = MemoryTaskQueue(completed_retention=1)
    first = queue.submit("demo.noop")
    second = queue.submit("demo.noop")
    pending = queue.submit("demo.noop")
    worker = Worker(queue, HANDLERS)
    await worker.run_once()
    await worker.run_once()
    with pytest.raises(AppError, match="不存在"):
        queue.get(first.id)
    assert queue.get(second.id).status == "succeeded"
    assert queue.get(pending.id).status == "queued"


async def test_unknown_handler_is_terminal_failure() -> None:
    queue = MemoryTaskQueue()
    record = queue.submit("unknown")
    await Worker(queue, HANDLERS).run_once()
    assert record.status == "failed"


async def test_shutdown_cancels_active_and_pending_tasks_and_closes_admission() -> None:
    queue = MemoryTaskQueue()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(record):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    first = queue.submit("slow")
    second = queue.submit("slow")
    worker = Worker(queue, {"slow": slow})
    await worker.start()
    await asyncio.wait_for(started.wait(), 1)
    await worker.stop(timeout=0.01)
    assert cancelled.is_set()
    assert first.status == second.status == "failed"
    await asyncio.wait_for(queue.join(), 1)
    with pytest.raises(AppError, match="停止接收"):
        queue.submit("slow")


async def test_task_public_state_carries_real_stage_and_failed_stage() -> None:
    """The stepper is driven by these fields, so they must survive serialization."""
    queue = MemoryTaskQueue()

    async def staged(record):
        record.stage, record.stage_detail = "vectorize", "8/24"
        record.skipped_stages = ["parse"]
        raise AppError("DOCUMENT_PROCESSING_FAILED", "向量写入失败", 503)

    record = queue.submit("staged", {"documentId": "document-1"})
    await Worker(queue, {"staged": staged}).run_once()
    public = queue.get(record.id).public()
    assert public["status"] == "failed"
    assert public["stage"] == "vectorize"
    assert public["stageDetail"] == "8/24"
    assert public["failedStage"] == "vectorize"
    assert public["skippedStages"] == ["parse"]
    assert public["documentId"] == "document-1"
    assert public["error"] == "向量写入失败"
