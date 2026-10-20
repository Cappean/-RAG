"""Persistence boundary helpers for knowledge records."""

from bson import ObjectId

from app.core.errors import AppError


def object_id(value: str) -> ObjectId:
    """Reject malformed IDs without sending database driver errors to clients."""
    if not ObjectId.is_valid(value):
        raise AppError("INVALID_ID", "文档或切片 ID 格式无效", 422)
    return ObjectId(value)


def document_view(record: dict) -> dict:
    """Expose only the public document fields; never leak storage credentials/keys."""
    return {
        "id": str(record["_id"]),
        **{
            key: record.get(key)
            for key in ("name", "fileName", "sourceType", "status", "createdAt", "updatedAt")
        },
    }
