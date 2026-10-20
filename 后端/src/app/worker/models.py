"""Public task state; task payloads are intentionally excluded from API responses."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict, repr=False)
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    progress: str | None = None
    # 结构化阶段：前端阶段条只读这些字段，不解析中文描述文案。
    stage: str | None = None
    stage_detail: str | None = None
    skipped_stages: list[str] = Field(default_factory=list)
    failed_stage: str | None = None

    def public(self) -> dict:
        return {
            "taskId": self.id,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "stageDetail": self.stage_detail,
            "skippedStages": list(self.skipped_stages),
            "failedStage": self.failed_stage,
            # 文档 ID 用于把任务卡片挂回对应文件；其余载荷仍不外泄。
            "documentId": self.payload.get("documentId"),
            "createdAt": self.created_at.isoformat(),
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
        }
