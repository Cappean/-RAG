"""Knowledge business workflows across MongoDB, MinIO, MinerU and Milvus."""

import asyncio
import hashlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.core.loggingUtils import get_logger
from app.modules.knowledge.model import document_view
from app.modules.knowledge.splitter import (
    DocumentSplitter,
    embedding_text,
    markdown_blocks,
    mineru_blocks,
)

logger = get_logger(__name__)


class KnowledgeService:
    """Originals are immutable; only fully committed documents become searchable."""

    def __init__(self, repository, objects, vectors, embedding, queue, mineru, settings):
        self.repository, self.objects, self.vectors = repository, objects, vectors
        self.embedding, self.queue, self.mineru, self.settings = embedding, queue, mineru, settings
        # Admission lock serializes local upload/retry decisions, not expensive ingestion.
        self._admission = asyncio.Lock()

    async def list_documents(self, status=None):
        return [document_view(row) for row in await self.repository.list(status)]

    async def _require(self, document_id):
        row = await self.repository.get(document_id)
        if row is None:
            raise AppError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
        return row

    async def get_document(self, document_id):
        return document_view(await self._require(document_id))

    async def ready_document_ids(self):
        return await self.repository.ready_document_ids()

    async def evidence(self, chunk_ids):
        return await self.repository.evidence(chunk_ids)

    ready_chunks = evidence

    async def recover_interrupted(self):
        return await self.repository.recover_interrupted()

    async def access_url(self, document_id):
        row = await self._require(document_id)
        seconds = self.settings.access_url_seconds
        url = await asyncio.to_thread(self.objects.presign, row["originalObjectKey"], seconds)
        return {
            "documentId": document_id,
            "sourceType": row["sourceType"],
            "url": url,
            "expiresIn": seconds,
        }

    async def upload(self, file_name: str, data: bytes):
        # Validate exact bytes before touching external stores, preserving hash identity.
        file_name = Path(file_name.replace("\\", "/")).name
        source = Path(file_name).suffix.lower().lstrip(".")
        if source not in {"pdf", "md"}:
            raise AppError("UNSUPPORTED_FILE_TYPE", "仅支持 PDF 和 Markdown 文件", 415)
        if not data:
            raise AppError("EMPTY_FILE", "上传文件为空", 422)
        if len(data) > self.settings.upload_max_bytes:
            raise AppError("FILE_TOO_LARGE", "文件超过上传大小限制", 413)
        if source == "pdf" and not data.lstrip().startswith(b"%PDF-"):
            raise AppError("INVALID_PDF", "文件不是有效的 PDF 格式", 422)
        if source == "md":
            try:
                data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise AppError("INVALID_MARKDOWN", "Markdown 文件必须使用 UTF-8 编码", 422) from exc
        digest = hashlib.sha256(data).hexdigest()
        async with self._admission:
            existing = await self.repository.by_hash(digest)
            if existing:
                logger.info(
                    "duplicate upload document_id=%s status=%s", existing["_id"], existing["status"]
                )
                return {"document": document_view(existing), "duplicate": True, "taskId": None}
            identifier, now = ObjectId(), datetime.now(UTC)
            key = f"knowledge/{identifier}/original.{source}"
            row = {
                "_id": identifier,
                "ownerId": None,
                "name": file_name,
                "fileName": file_name,
                "sourceType": source,
                "contentHash": digest,
                "status": "processing",
                "originalObjectKey": key,
                "parserOutputPrefix": None,
                "createdAt": now,
                "updatedAt": now,
            }
            # Upload precedes metadata; failed uploads never create a false accepted record.
            await asyncio.to_thread(
                self.objects.put,
                key,
                data,
                "application/pdf" if source == "pdf" else "text/markdown; charset=utf-8",
            )
            try:
                await self.repository.create(row)
            except DuplicateKeyError:
                existing = await self.repository.by_hash(digest)
                if existing:
                    return {"document": document_view(existing), "duplicate": True, "taskId": None}
                raise
            try:
                task = self.queue.submit("knowledge.ingest", {"documentId": str(identifier)})
            except Exception:
                await self.repository.update(str(identifier), status="failed")
                logger.info("document queue admission failed document_id=%s", identifier)
                raise
            logger.info(
                "document upload accepted document_id=%s bytes=%s task_id=%s",
                identifier,
                len(data),
                task.id,
            )
            return {"document": document_view(row), "duplicate": False, "taskId": task.id}

    async def retry(self, document_id):
        async with self._admission:
            row = await self._require(document_id)
            if row["status"] != "failed":
                raise AppError("DOCUMENT_NOT_FAILED", "只有失败文档可以重新提交处理", 409)
            row = await self.repository.claim_failed(document_id)
            if row is None:
                raise AppError("DOCUMENT_PROCESSING_CONFLICT", "文档处理状态已变化，请刷新", 409)
            try:
                task = self.queue.submit("knowledge.ingest", {"documentId": document_id})
            except Exception:
                await self.repository.update(document_id, status="failed")
                raise
            logger.info("document retry accepted document_id=%s task_id=%s", document_id, task.id)
            return {"document": document_view(row), "duplicate": False, "taskId": task.id}

    async def process_document(self, document_id, progress=None):
        """Run one job; optional progress callback updates its in-memory task record."""

        def report(stage):
            if progress is not None:
                progress(stage)
            logger.debug("document stage document_id=%s stage=%s", document_id, stage)

        row = await self._require(document_id)
        if row["status"] != "processing":
            raise AppError("DOCUMENT_NOT_PROCESSING", "文档当前未处于处理状态", 409)
        try:
            if (
                row["sourceType"] == "pdf"
                and not row.get("parserOutputPrefix")
                and self.mineru is None
            ):
                raise AppError("MINERU_UNAVAILABLE", "PDF 解析服务未配置，暂时无法处理 PDF", 503)
            # Idempotent retry cleanup: failed/partial records are never used for RAG.
            report("清理未完成的索引")
            await asyncio.to_thread(self.vectors.delete, document_id)
            await self.repository.replace_chunks(document_id, [])
            data = await asyncio.to_thread(self.objects.get, row["originalObjectKey"])
            if row["sourceType"] == "md":
                report("读取 Markdown 原文")
                blocks = await asyncio.to_thread(markdown_blocks, data.decode("utf-8-sig"))
            else:
                if row.get("parserOutputPrefix"):
                    report("复用已保存的 MinerU 解析产物")
                    blocks = await self._saved_pdf_blocks(row["parserOutputPrefix"])
                else:
                    report("MinerU 解析 PDF")
                    # Keep temp input alive until the synchronous parser thread terminates.
                    result = await self._parse_pdf(data, row["fileName"])
                    if result.content_list_v2_path is None:
                        raise AppError(
                            "PARSER_OUTPUT_MISSING", "MinerU 缺少唯一的 content_list_v2 产物", 422
                        )
                    prefix = f"knowledge/{document_id}/parser-output/"
                    report("保存解析产物")
                    await asyncio.to_thread(self.objects.upload_tree, result.output_dir, prefix)
                    await self.repository.update(document_id, parserOutputPrefix=prefix)
                    blocks = await asyncio.to_thread(mineru_blocks, result.content_list_v2_path)
            splitter = DocumentSplitter(
                self.embedding.count_tokens,
                self.settings.chunk_target_tokens,
                self.settings.chunk_max_tokens,
                self.settings.chunk_overlap_tokens,
            )
            report("按标题与 token 切分")
            chunks = await asyncio.to_thread(splitter.split, blocks)
            if not chunks:
                raise AppError("DOCUMENT_EMPTY", "文档没有可用于检索的文字内容", 422)
            now = datetime.now(UTC)
            for chunk in chunks:
                chunk.update(
                    {"_id": ObjectId(), "documentId": ObjectId(document_id), "createdAt": now}
                )
            await self.repository.replace_chunks(document_id, chunks)
            # Bound inference batch memory, and only mark ready after every vector succeeds.
            for start in range(0, len(chunks), 8):
                report(f"向量入库 {start}/{len(chunks)}")
                batch = chunks[start : start + 8]
                encodings = await asyncio.to_thread(
                    self.embedding.encode,
                    [embedding_text(c["content"], c["headingPath"]) for c in batch],
                )
                if len(encodings) != len(batch):
                    raise AppError("EMBEDDING_INVALID", "向量模型返回数量不匹配", 503)
                vectors = [
                    {"chunkId": str(chunk["_id"]), "documentId": document_id, **encoding}
                    for chunk, encoding in zip(batch, encodings, strict=True)
                ]
                await asyncio.to_thread(self.vectors.upsert, vectors)
                logger.debug(
                    "document vectors stored document_id=%s completed=%s total=%s",
                    document_id,
                    min(start + 8, len(chunks)),
                    len(chunks),
                )
            await self.repository.update(document_id, status="ready")
            report("入库完成")
            logger.info("document ready document_id=%s chunks=%s", document_id, len(chunks))
            return {"documentId": document_id, "chunkCount": len(chunks)}
        except BaseException as exc:
            # Cancellation also marks the persisted document failed.
            await self.repository.update(document_id, status="failed")
            logger.info(
                "document processing failed document_id=%s exception_type=%s",
                document_id,
                type(exc).__name__,
            )
            if isinstance(exc, (AppError, asyncio.CancelledError)):
                raise
            raise AppError(
                "DOCUMENT_PROCESSING_FAILED", "文档处理失败，请检查依赖后重新提交", 503
            ) from exc

    async def _parse_pdf(self, data, file_name):
        """Thread owns its input lifetime even if its awaiting task is cancelled."""

        def parse():
            with tempfile.TemporaryDirectory(prefix="medical-knowledge-") as directory:
                path = Path(directory) / file_name
                path.write_bytes(data)
                return self.mineru.parse(path)

        return await asyncio.to_thread(parse)

    async def _saved_pdf_blocks(self, prefix):
        """Reuse the immutable, uniquely matched structured artifact on retries."""
        keys = await asyncio.to_thread(self.objects.list, prefix)
        candidates = [
            key
            for key in keys
            if key.startswith(prefix) and Path(key).name.endswith("content_list_v2.json")
        ]
        if len(candidates) != 1:
            raise AppError(
                "PARSER_OUTPUT_MISSING", "保存的 MinerU 产物缺少唯一 content_list_v2 文件", 422
            )
        data = await asyncio.to_thread(self.objects.get, candidates[0])

        def parse_saved():
            with tempfile.TemporaryDirectory(prefix="medical-knowledge-reuse-") as directory:
                path = Path(directory) / "content_list_v2.json"
                path.write_bytes(data)
                return mineru_blocks(path)

        return await asyncio.to_thread(parse_saved)
