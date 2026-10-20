"""Read-only public task status API."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}")
async def task_status(task_id: str, request: Request) -> dict:
    return request.app.state.task_queue.get(task_id).public()
