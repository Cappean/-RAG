"""Isolated lifecycle tests; these do not claim external RAG acceptance."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.core.errors import AppError
from app.modules.consultation.schema import ConversationInput
from app.modules.consultation.service import ConsultationService


class MemoryRepository:
    """A deliberately isolated store for failure and cancellation contracts."""

    def __init__(self):
        self.conversations = {}
        self.messages = {}

    async def insert(self, record):
        self.conversations[record["_id"]] = deepcopy(record)

    async def get(self, identifier):
        return self.conversations.get(identifier)

    async def list(self, patient_id=None):
        return list(self.conversations.values())

    async def history(self, identifier):
        return [
            deepcopy(item)
            for item in self.messages.values()
            if item["conversationId"] == identifier
        ]

    async def insert_message(self, record):
        self.messages[record["_id"]] = deepcopy(record)

    async def update_message(self, identifier, fields):
        if identifier in self.messages:
            self.messages[identifier].update(deepcopy(fields))

    async def touch(self, identifier, now):
        self.conversations[identifier]["updatedAt"] = now

    async def recover(self, now):
        count = 0
        for item in self.messages.values():
            if item["status"] == "generating":
                item.update(status="failed", updatedAt=now)
                count += 1
        return count


class Patients:
    async def get(self, identifier):
        return {"id": identifier, "allergies": [], "medicalHistory": [], "medications": []}


class Rag:
    def __init__(self, fail=False):
        self.fail = fail
        self.histories = []

    async def prepare(self, question, history, patient):
        self.histories.append(deepcopy(history))
        return {
            "citations": [{"chunkId": "real-shape-only-test", "quote": "测试原文"}],
            "retrievalMs": 1,
        }

    async def stream(self, prepared):
        yield "测试增量"
        if self.fail:
            raise RuntimeError("simulated upstream failure for isolated test")
        yield "结束"


async def make_service(rag=None):
    repo = MemoryRepository()
    service = ConsultationService(repo, Patients(), rag or Rag(), SimpleNamespace())
    conversation = await service.create(ConversationInput(patientId=str(ObjectId())))
    return service, repo, conversation["id"]


async def test_multiturn_completed_history_and_quotes_survive_new_service():
    rag = Rag()
    service, repo, identifier = await make_service(rag)
    turn = await service.begin(identifier, "第一个测试问题")
    events = [event async for event in turn.events()]
    assert events[-1][0] == "done"
    assert len(repo.messages) == 2
    second = await service.begin(identifier, "接着追问")
    assert [event async for event in second.events()][-1][0] == "done"
    assert len(rag.histories[1]) == 2
    restarted = ConsultationService(repo, Patients(), rag, SimpleNamespace())
    history = await restarted.history(identifier)
    assert len(history) == 4
    assert history[1]["citations"][0]["quote"] == "测试原文"
    assert all(item["status"] == "completed" for item in history)


async def test_conflict_is_before_duplicate_user_insert():
    service, repo, identifier = await make_service()
    turn = await service.begin(identifier, "测试问题")
    with pytest.raises(AppError) as caught:
        await service.begin(identifier, "重复点击")
    assert caught.value.status_code == 409
    assert len(repo.messages) == 2
    await turn.close()
    assert not service.active


async def test_object_id_spelling_cannot_bypass_conversation_reservation():
    service, repo, identifier = await make_service()
    turn = await service.begin(identifier, "测试问题")
    with pytest.raises(AppError) as caught:
        await service.begin(identifier.upper(), "大写标识并发")
    assert caught.value.status_code == 409
    assert len(repo.messages) == 2
    await turn.close()


async def test_upstream_failure_retains_partial_and_allows_new_turn():
    service, repo, identifier = await make_service(Rag(fail=True))
    turn = await service.begin(identifier, "失败测试")
    events = [item async for item in turn.events()]
    assert events[-1][0] == "error"
    history = await service.history(identifier)
    assert history[1]["status"] == "failed"
    assert history[1]["content"] == "测试增量"
    assert not service.active


async def test_generator_close_retains_partial_and_marks_failed():
    service, repo, identifier = await make_service()
    turn = await service.begin(identifier, "断连测试")
    stream = turn.events()
    while True:
        event, _ = await anext(stream)
        if event == "token":
            break
    await stream.aclose()
    assistant = list(repo.messages.values())[1]
    assert assistant["status"] == "failed"
    assert assistant["content"] == "测试增量"
    assert not service.active


async def test_timeout_and_recovery_do_not_claim_completion():
    class HangingRag(Rag):
        async def stream(self, prepared):
            yield "超时前"
            await asyncio.Event().wait()

    service, repo, identifier = await make_service(HangingRag())
    service.settings.generation_timeout_seconds = 0.01
    turn = await service.begin(identifier, "超时测试")
    events = [item async for item in turn.events()]
    assert events[-1][1]["code"] == "GENERATION_TIMEOUT"
    assert list(repo.messages.values())[1]["content"] == "超时前"
    abandoned = await service.begin(identifier, "重启测试")
    restarted = ConsultationService(repo, Patients(), Rag(), SimpleNamespace())
    assert await restarted.recover() == 1
    assert list(repo.messages.values())[-1]["status"] == "failed"
    await abandoned.close()


async def test_unconfirmed_unlinked_policy_is_explicit():
    service = ConsultationService(MemoryRepository(), Patients(), Rag(), SimpleNamespace())
    with pytest.raises(AppError) as caught:
        await service.create(ConversationInput())
    assert caught.value.code == "PATIENT_SELECTION_REQUIRED"


async def test_admission_cleanup_write_failure_still_releases_active():
    service, repo, identifier = await make_service()

    async def fail(*args):
        raise RuntimeError("isolated write outage")

    repo.insert_message = fail
    repo.update_message = fail
    with pytest.raises(RuntimeError):
        await service.begin(identifier, "保存失败测试")
    assert not service.active


async def test_stream_persistence_failure_is_explicit_terminal_error():
    service, repo, identifier = await make_service()
    turn = await service.begin(identifier, "保存失败测试")

    async def fail(*args):
        raise RuntimeError("isolated write outage")

    repo.update_message = fail
    events = [event async for event in turn.events()]
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "MESSAGE_PERSISTENCE_FAILED"
    assert all(event[0] != "done" for event in events)
    assert not service.active
