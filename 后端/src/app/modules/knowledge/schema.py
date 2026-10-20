"""Public knowledge API contracts (camelCase matches the stored design)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DocumentStatus = Literal["processing", "ready", "failed", "disabled"]


class DocumentResponse(BaseModel):
    id: str
    name: str
    fileName: str
    sourceType: Literal["pdf", "md"]
    status: DocumentStatus
    createdAt: datetime
    updatedAt: datetime


class UploadResponse(BaseModel):
    document: DocumentResponse
    duplicate: bool = False
    taskId: str | None = None


class AccessResponse(BaseModel):
    documentId: str
    sourceType: Literal["pdf", "md"]
    url: str
    expiresIn: int
