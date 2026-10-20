"""HTTP-only document upload/query/access endpoints."""

from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from app.modules.knowledge.schema import (
    AccessResponse,
    DocumentResponse,
    DocumentStatus,
    UploadResponse,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _service(request):
    return request.app.state.services.knowledge


@router.get("/sources", response_model=list[DocumentResponse])
async def sources(request: Request):
    return await _service(request).list_documents("ready")


@router.get("/documents", response_model=list[DocumentResponse])
async def documents(request: Request, status: DocumentStatus | None = None):
    return await _service(request).list_documents(status)


@router.post("/documents", response_model=UploadResponse, status_code=202)
async def upload(request: Request, file: Annotated[UploadFile, File()]):
    service = _service(request)
    # Read one byte beyond the limit to reject oversize uploads without unbounded RAM.
    data = await file.read(request.app.state.settings.upload_max_bytes + 1)
    return await service.upload(file.filename or "", data)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def document(request: Request, document_id: str):
    return await _service(request).get_document(document_id)


@router.get("/documents/{document_id}/access-url", response_model=AccessResponse)
async def access(request: Request, document_id: str):
    return await _service(request).access_url(document_id)


@router.post("/documents/{document_id}/retry", response_model=UploadResponse, status_code=202)
async def retry(request: Request, document_id: str):
    return await _service(request).retry(document_id)
