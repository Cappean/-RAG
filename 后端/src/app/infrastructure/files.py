"""Same-origin expiring object gateway; preserves PDF byte-range semantics."""

import asyncio
import mimetypes
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.errors import AppError
from app.core.loggingUtils import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/objects")
async def read_object(request: Request, key: str, expires: int, signature: str):
    objects = getattr(request.app.state, "objects", None)
    if objects is None:
        raise AppError("OBJECT_STORE_UNAVAILABLE", "原文存储暂不可用", 503)
    objects.verify(key, expires, signature)
    try:
        data = await asyncio.to_thread(objects.get, key)
    except Exception as exc:
        logger.warning("object read failed exception_type=%s", type(exc).__name__)
        raise AppError("OBJECT_READ_FAILED", "原文件不可用，请检查存储服务", 503) from exc
    length = len(data)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    status = 200
    byte_range = request.headers.get("Range")
    if byte_range:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
        if not match or not any(match.groups()):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{length}"})
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last) if last else length - 1, length - 1)
        else:
            start, end = max(length - int(last), 0), length - 1
        if start > end or start >= length:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{length}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{length}"
        data, status = data[start : end + 1], 206
    return Response(
        data,
        status_code=status,
        headers=headers,
        media_type=mimetypes.guess_type(key)[0] or "application/octet-stream",
    )
