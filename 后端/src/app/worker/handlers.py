"""Task registry: business modules register explicit handlers during app assembly."""

import asyncio
from collections.abc import Awaitable, Callable

from app.worker.models import TaskRecord

TaskHandler = Callable[[TaskRecord], Awaitable[dict | None]]


async def demo_noop(task: TaskRecord) -> dict:
    """Internal test handler; no arbitrary handler execution endpoint is exposed."""
    await asyncio.sleep(min(max(float(task.payload.get("seconds", 0)), 0), 5))
    return {"processed": True, "taskId": task.id}


HANDLERS: dict[str, TaskHandler] = {"demo.noop": demo_noop}
