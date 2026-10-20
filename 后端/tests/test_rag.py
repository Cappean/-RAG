"""RAG boundary tests use explicit adapters, never external credentials/services."""

from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.modules.rag.service import RagService


class Embedding:
    def truncate(self, text, max_tokens):
        return text[:max_tokens]

    def encode(self, texts):
        self.texts = texts
        return [{"denseVector": [0.1], "sparseVector": {12: 0.7}}]


class Vectors:
    def search(self, dense, sparse, document_ids, limit):
        assert dense == [0.1]
        assert sparse == {12: 0.7}
        assert document_ids == ["ready-document"]
        return ["chunk-2", "disabled-chunk", "chunk-1"]


class Knowledge:
    async def ready_document_ids(self):
        return ["ready-document"]

    async def evidence(self, ids):
        # Emulates service-level readiness filtering, including stale index hits.
        return [
            {
                "chunkId": identifier,
                "documentId": "ready-document",
                "documentName": "明确标识的测试文档",
                "content": f"测试原文 {identifier}",
                "headingPath": ["测试章节"],
                "sourceLocations": [{"startLine": 3, "endLine": 4}],
            }
            for identifier in ids
            if identifier != "disabled-chunk"
        ]


class Model:
    async def stream(self, messages):
        yield "测试真实协议形状"


async def test_dense_sparse_query_history_evidence_and_locations_preserved():
    embedding = Embedding()
    rag = RagService(embedding, Vectors(), Knowledge(), Model(), SimpleNamespace())
    history = [
        {"role": "user", "content": "既往问题", "status": "completed"},
        {"role": "assistant", "content": "失败回答不应送模型", "status": "failed"},
    ]
    prepared = await rag.prepare("接着呢", history, None)
    assert "既往问题" in embedding.texts[0]
    assert prepared["messages"][-1]["content"] == "接着呢"
    assert all(item["content"] != "失败回答不应送模型" for item in prepared["messages"])
    assert [item["chunkId"] for item in prepared["citations"]] == ["chunk-2", "chunk-1"]
    assert prepared["citations"][0]["quote"] == "测试原文 chunk-2"
    assert prepared["citations"][0]["sourceLocations"] == [{"startLine": 3, "endLine": 4}]


async def test_no_evidence_does_not_call_model_or_fake_success():
    class EmptyKnowledge(Knowledge):
        async def ready_document_ids(self):
            return []

    rag = RagService(Embedding(), Vectors(), EmptyKnowledge(), Model(), SimpleNamespace())
    with pytest.raises(AppError) as caught:
        await rag.prepare("测试", [], None)
    assert caught.value.code == "NO_EVIDENCE"


async def test_dependency_failure_is_not_reported_as_no_evidence():
    class FailedVectors(Vectors):
        def search(self, *args):
            raise RuntimeError("isolated dependency failure")

    rag = RagService(Embedding(), FailedVectors(), Knowledge(), Model(), SimpleNamespace())
    with pytest.raises(RuntimeError):
        await rag.prepare("测试", [], None)


async def test_zero_history_setting_and_query_budget_are_effective():
    embedding = Embedding()
    rag = RagService(
        embedding,
        Vectors(),
        Knowledge(),
        Model(),
        SimpleNamespace(rag_history_messages=0, rag_query_tokens=4),
    )
    prepared = await rag.prepare(
        "123456789", [{"role": "user", "content": "old", "status": "completed"}], None
    )
    assert embedding.texts == ["1234"]
    assert len(prepared["messages"]) == 2


async def test_nonexistent_citation_number_fails_even_across_stream_chunks():
    class InvalidCitationModel(Model):
        async def stream(self, messages):
            yield "错误编号["
            yield "99]"

    rag = RagService(Embedding(), Vectors(), Knowledge(), InvalidCitationModel(), SimpleNamespace())
    prepared = await rag.prepare("测试", [], None)
    with pytest.raises(AppError) as caught:
        _ = [token async for token in rag.stream(prepared)]
    assert caught.value.code == "INVALID_MODEL_CITATION"
