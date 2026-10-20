"""Workflow failure/dedup tests with explicit in-memory external replacements."""

import asyncio
import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.modules.knowledge.service import KnowledgeService
from app.worker.queue import MemoryTaskQueue


class Repository:
    def __init__(self):
        self.rows, self.chunks = {}, []

    async def by_hash(self, value):
        return next((r for r in self.rows.values() if r["contentHash"] == value), None)

    async def create(self, row):
        self.rows[str(row["_id"])] = row

    async def get(self, identifier):
        return self.rows.get(identifier)

    async def update(self, identifier, **values):
        self.rows[identifier].update(values)

    async def replace_chunks(self, identifier, chunks):
        self.chunks = chunks

    async def claim_failed(self, identifier):
        row = self.rows[identifier]
        if row["status"] == "failed":
            row["status"] = "processing"
            return row


class Objects:
    def __init__(self):
        self.data = {}
        self.file_uploads = 0

    def put(self, key, data, content_type):
        self.data[key] = data

    def put_file(self, key, path, content_type):
        # 生产路径按文件上传；这里读回字节，断言"没有整块字节进内存"仍可验证调用次数。
        self.file_uploads += 1
        self.data[key] = Path(path).read_bytes()

    def get(self, key):
        return self.data[key]

    def download_to(self, key, path):
        Path(path).write_bytes(self.data[key])

    def list(self, prefix):
        return [key for key in self.data if key.startswith(prefix)]


class Vectors:
    def __init__(self):
        self.rows = []
        self.fail = False

    def delete(self, identifier):
        self.rows = []

    def upsert(self, rows):
        if self.fail:
            raise RuntimeError("private dependency details")
        self.rows.extend(rows)


def service():
    settings = SimpleNamespace(
        upload_max_bytes=1024,
        chunk_target_tokens=500,
        chunk_max_tokens=800,
        chunk_overlap_tokens=80,
    )
    embedding = SimpleNamespace(
        count_tokens=len,
        encode=lambda texts: [{"denseVector": [1.0], "sparseVector": {1: 1.0}} for text in texts],
    )
    return KnowledgeService(
        Repository(), Objects(), Vectors(), embedding, MemoryTaskQueue(1), None, settings
    )


async def test_exact_bytes_deduplicate_without_task():
    sut = service()
    first = await sut.upload("test.md", b"# test\n\ncontent")
    again = await sut.upload("renamed.md", b"# test\n\ncontent")
    assert again["document"]["id"] == first["document"]["id"]
    assert again["duplicate"] and again["taskId"] is None
    assert len(sut.objects.data) == 1


async def test_queue_full_marks_record_failed_and_does_not_claim_accepted():
    sut = service()
    await sut.upload("one.md", b"one")
    with pytest.raises(AppError) as exc:
        await sut.upload("two.md", b"two")
    assert exc.value.code == "TASK_QUEUE_FULL"
    assert sorted(row["status"] for row in sut.repository.rows.values()) == ["failed", "processing"]


async def test_vector_failure_persists_failed_and_retry_cleans_partial_vectors():
    sut = service()
    upload = await sut.upload("demo.md", b"# Header\n\nReal input text")
    identifier = upload["document"]["id"]
    sut.vectors.fail = True
    with pytest.raises(AppError) as exc:
        await sut.process_document(identifier)
    assert exc.value.code == "DOCUMENT_PROCESSING_FAILED"
    assert sut.repository.rows[identifier]["status"] == "failed"
    assert "private" not in exc.value.message
    # Consume original queued item to make capacity available for the explicit retry.
    record = await sut.queue.take()
    sut.queue.finish(record)
    retried = await sut.retry(identifier)
    assert retried["taskId"] != upload["taskId"]
    sut.vectors.fail = False
    result = await sut.process_document(identifier)
    assert result["chunkCount"] == 1
    assert sut.repository.rows[identifier]["status"] == "ready"
    assert len(sut.vectors.rows) == 1


async def test_validation_does_not_write_external_storage():
    sut = service()
    for name, data in [("fake.pdf", b"not pdf"), ("bad.md", b"\xff"), ("bad.txt", b"x")]:
        with pytest.raises(AppError):
            await sut.upload(name, data)
    assert not sut.objects.data


async def test_missing_pdf_parser_fails_with_clear_dependency_error():
    sut = service()
    result = await sut.upload("test.pdf", b"%PDF-1.7\nexplicit test bytes")
    identifier = result["document"]["id"]
    with pytest.raises(AppError) as exc:
        await sut.process_document(identifier)
    assert exc.value.code == "MINERU_UNAVAILABLE"
    assert exc.value.status_code == 503
    assert sut.repository.rows[identifier]["status"] == "failed"


async def test_empty_markdown_cannot_be_ready():
    sut = service()
    result = await sut.upload("empty.md", b"# Only a heading\n\n")
    identifier = result["document"]["id"]
    with pytest.raises(AppError) as exc:
        await sut.process_document(identifier)
    assert exc.value.code == "DOCUMENT_EMPTY"
    assert sut.repository.rows[identifier]["status"] == "failed"


async def test_markdown_reports_only_executed_stages_with_real_counts():
    """Markdown has no parser step, so the stepper must never claim one ran."""
    sut = service()
    result = await sut.upload("progress.md", b"# Heading\n\nExplicit test text")
    stages = []
    await sut.process_document(
        result["document"]["id"], lambda stage, detail=None: stages.append((stage, detail))
    )
    assert [stage for stage, _ in stages] == ["split", "vectorize"]
    assert stages[-1] == ("vectorize", "1/1")
    assert sut.vectors.rows


async def test_pdf_retry_reuses_unique_saved_parser_output_without_mineru():
    sut = service()
    result = await sut.upload("saved.pdf", b"%PDF-1.7\nreuse test")
    identifier = result["document"]["id"]
    prefix = f"knowledge/{identifier}/parser-output/"
    sut.repository.rows[identifier]["parserOutputPrefix"] = prefix
    sut.objects.data[prefix + "auto/source_content_list_v2.json"] = (
        b'[[{"type":"paragraph","content":{"paragraph_content":"Saved parsed text"}}]]'
    )
    stages = []
    await sut.process_document(
        identifier, lambda stage, detail=None: stages.append((stage, detail))
    )
    assert sut.repository.rows[identifier]["status"] == "ready"
    assert sut.repository.chunks[0]["content"] == "Saved parsed text"
    assert ("parse", "复用已保存的解析产物") in stages
    assert all(detail != "MinerU 解析 PDF" for _, detail in stages)
    assert sut.mineru is None


@pytest.mark.parametrize("count", [0, 2])
async def test_pdf_saved_parser_missing_or_ambiguous_is_failed_without_cloud_fallback(count):
    sut = service()
    result = await sut.upload("saved.pdf", b"%PDF-1.7\nambiguity test")
    identifier = result["document"]["id"]
    prefix = f"knowledge/{identifier}/parser-output/"
    sut.repository.rows[identifier]["parserOutputPrefix"] = prefix
    for number in range(count):
        sut.objects.data[prefix + f"{number}_content_list_v2.json"] = b"[]"
    with pytest.raises(AppError) as exc:
        await sut.process_document(identifier)
    assert exc.value.code == "PARSER_OUTPUT_MISSING"
    assert sut.repository.rows[identifier]["status"] == "failed"
    assert not sut.vectors.rows


async def test_parser_cancellation_keeps_temp_alive_but_never_publishes_late_result(tmp_path):
    sut = service()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    path_observed = []
    output = tmp_path / "parser-result"
    output.mkdir()
    structure = output / "content_list_v2.json"
    structure.write_text('[[{"type":"paragraph","content":{"paragraph_content":"test"}}]]')

    def parse(path):
        path_observed.append(Path(path))
        started.set()
        assert release.wait(5), "Test parser was not released"
        assert Path(path).exists(), "Cancellation removed temp input before parser finished"
        finished.set()
        return SimpleNamespace(output_dir=output, content_list_v2_path=structure)

    sut.mineru = SimpleNamespace(parse=parse)
    result = await sut.upload("test.pdf", b"%PDF-1.7\nexplicit cancellation test")
    identifier = result["document"]["id"]
    job = asyncio.create_task(sut.process_document(identifier))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert sut.repository.rows[identifier]["status"] == "failed"
        assert not sut.vectors.rows
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    # The parser thread can finish locally, but the cancelled orchestration does
    # not upload artifacts, insert chunks, store vectors or mark ready.
    assert sut.repository.rows[identifier]["parserOutputPrefix"] is None
    assert not sut.repository.chunks
    assert not sut.vectors.rows


async def test_oversize_upload_names_the_limit_and_writes_nothing():
    sut = service()  # 测试配置上限 1024 字节
    with pytest.raises(AppError) as exc:
        await sut.upload("big.md", b"x" * 2048)
    assert exc.value.code == "FILE_TOO_LARGE"
    # 提示必须带上限值，否则用户不知道要压到多小。
    assert "1 KB" in exc.value.message
    assert not sut.objects.data
    assert not sut.repository.rows


async def test_production_path_uploads_from_file_not_bytes(tmp_path):
    sut = service()
    body = "# 标题\n\n" + "正文" * 50
    path = tmp_path / "from-disk.md"
    path.write_bytes(body.encode("utf-8"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = await sut.upload_file("from-disk.md", path, len(body.encode()), digest)
    assert result["duplicate"] is False
    # 走的是按文件上传，不再把全文包进 BytesIO。
    assert sut.objects.file_uploads == 1
    key = f"knowledge/{result['document']['id']}/original.md"
    assert sut.objects.data[key] == body.encode()
