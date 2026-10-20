"""Encapsulate queue admission and bounded terminal state retention."""

import asyncio
from collections import deque
from datetime import UTC, datetime

from app.core.errors import AppError
from app.core.loggingUtils import get_logger
from app.worker.models import TaskRecord

logger = get_logger(__name__)


class MemoryTaskQueue:
    """All mutations happen on the same event loop, with no await during admission."""

    def __init__(self, capacity: int = 32, completed_retention: int = 256):
        if capacity < 1 or completed_retention < 1:
            raise ValueError("Queue capacity and retention must be positive")
        self._queue: asyncio.Queue[TaskRecord] = asyncio.Queue(maxsize=capacity)
        self._records: dict[str, TaskRecord] = {}
        self._completed: deque[str] = deque()
        self._retention = completed_retention
        self.accepting = True

    def submit(self, kind: str, payload: dict | None = None) -> TaskRecord:
        if not self.accepting:
            raise AppError("TASK_QUEUE_CLOSED", "后台任务已停止接收", 503)
        record = TaskRecord(kind=kind, payload=payload or {})
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull as exc:
            logger.info("task rejected queue_full kind=%s", kind)
            raise AppError("TASK_QUEUE_FULL", "后台任务队列已满，请稍后再试", 503) from exc
        self._records[record.id] = record
        logger.info("task accepted task_id=%s kind=%s", record.id, kind)
        return record

    def get(self, task_id: str) -> TaskRecord:
        if task_id not in self._records:
            raise AppError("TASK_NOT_FOUND", "任务不存在、已过期或服务已重启", 404)
        return self._records[task_id]

    async def take(self) -> TaskRecord:
        return await self._queue.get()

    def finish(self, record: TaskRecord) -> None:
        record.completed_at = datetime.now(UTC)
        self._completed.append(record.id)
        while len(self._completed) > self._retention:
            self._records.pop(self._completed.popleft(), None)
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    def fail_pending(self) -> None:
        """Shutdown marks unstarted jobs failed rather than leaving false queued states."""
        while True:
            try:
                record = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            record.status, record.error = "failed", "服务关闭，任务未完成"
            self.finish(record)
