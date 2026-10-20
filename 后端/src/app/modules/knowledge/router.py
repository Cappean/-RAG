"""HTTP-only document upload/query/access endpoints."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from app.common.sizes import human_size
from app.core.errors import AppError
from app.modules.knowledge.schema import (
    AccessResponse,
    DocumentResponse,
    DocumentStatus,
    UploadResponse,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# 分块大小决定上传期间的内存上限：无论文件多大，RAM 里只有这一块。
CHUNK_BYTES = 1024 * 1024


def _service(request):
    return request.app.state.services.knowledge


def _spool(source, path: Path, limit: int) -> tuple[int, str]:
    """把上传流写进临时文件，边写边算 SHA-256；超限立即中止。

    这个函数是同步的，由调用方放进线程执行，避免阻塞事件循环。
    """
    digest = hashlib.sha256()
    size = 0
    source.seek(0)
    with path.open("wb") as target:
        while chunk := source.read(CHUNK_BYTES):
            size += len(chunk)
            if size > limit:
                raise AppError(
                    "FILE_TOO_LARGE",
                    f"文件超过 {human_size(limit)} 上传上限，请压缩或拆分后重试",
                    413,
                )
            digest.update(chunk)
            target.write(chunk)
    return size, digest.hexdigest()


@router.get("/limits")
async def upload_limits(request: Request):
    """前端据此提前拦截超大文件，不必先传满再被拒。"""
    limit = request.app.state.settings.upload_max_bytes
    return {"uploadMaxBytes": limit, "uploadMaxLabel": human_size(limit)}


@router.get("/sources", response_model=list[DocumentResponse])
async def sources(request: Request):
    return await _service(request).list_documents("ready")


@router.get("/documents", response_model=list[DocumentResponse])
async def documents(request: Request, status: DocumentStatus | None = None):
    return await _service(request).list_documents(status)


@router.post("/documents", response_model=UploadResponse, status_code=202)
async def upload(request: Request, file: Annotated[UploadFile, File()]):
    service = _service(request)
    limit = request.app.state.settings.upload_max_bytes
    suffix = Path(file.filename or "").suffix
    descriptor, name = tempfile.mkstemp(prefix="medical-upload-", suffix=suffix)
    os.close(descriptor)
    path = Path(name)
    try:
        size, digest = await asyncio.to_thread(_spool, file.file, path, limit)
        return await service.upload_file(file.filename or "", path, size, digest)
    finally:
        # 临时文件只服务于本次请求，异常路径同样要清掉；删除放到线程里避免阻塞。
        await asyncio.to_thread(path.unlink, missing_ok=True)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def document(request: Request, document_id: str):
    return await _service(request).get_document(document_id)


@router.get("/documents/{document_id}/access-url", response_model=AccessResponse)
async def access(request: Request, document_id: str):
    return await _service(request).access_url(document_id)


@router.post("/documents/{document_id}/retry", response_model=UploadResponse, status_code=202)
async def retry(request: Request, document_id: str):
    return await _service(request).retry(document_id)
