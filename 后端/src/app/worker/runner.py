"""Coroutine worker with per-task failure isolation and bounded shutdown."""

import asyncio

from app.core.errors import AppError
from app.core.loggingUtils import get_logger
from app.worker.handlers import TaskHandler
from app.worker.queue import MemoryTaskQueue

logger = get_logger(__name__)


class Worker:
    def __init__(self, queue: MemoryTaskQueue, handlers: dict[str, TaskHandler], concurrency=1):
        self.queue, self.handlers, self.concurrency = queue, handlers, concurrency
        self._tasks: list[asyncio.Task] = []

    async def run_once(self) -> None:
        record = await self.queue.take()
        record.status = "running"
        logger.debug("task running task_id=%s kind=%s", record.id, record.kind)
        try:
            handler = self.handlers.get(record.kind)
            if handler is None:
                raise AppError("TASK_HANDLER_NOT_FOUND", "任务处理器不存在")
            record.result = await handler(record)
            record.status = "succeeded"
        except asyncio.CancelledError:
            record.status, record.error = "failed", "服务关闭，任务未完成"
            raise
        except Exception as exc:
            record.status = "failed"
            record.error = (
                exc.message if isinstance(exc, AppError) else "任务处理失败，请查看服务日志"
            )
            logger.error("task failed task_id=%s exception_type=%s", record.id, type(exc).__name__)
        finally:
            self.queue.finish(record)
            logger.info("task finished task_id=%s status=%s", record.id, record.status)

    async def _consume(self) -> None:
        while True:
            await self.run_once()

    async def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._consume(), name=f"worker-{i}")
            for i in range(self.concurrency)
        ]
        logger.info("memory worker started concurrency=%s", self.concurrency)

    async def stop(self, timeout: float = 10) -> None:  # noqa: ASYNC109
        """Own the cleanup timeout so caller cancellation cannot skip task cancellation."""
        self.queue.accepting = False
        try:
            await asyncio.wait_for(self.queue.join(), timeout)
        except TimeoutError:
            logger.info("worker shutdown timeout; cancelling remaining tasks")
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            self.queue.fail_pending()
            logger.info("memory worker stopped")
