"""HTTP and disconnect integration with isolated stores, no external acceptance claim."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, Request

from app.core.errors import AppError, register_error_handlers
from app.modules.consultation.router import router as consultation_router
from app.modules.consultation.service import ConsultationService
from app.modules.patient.router import router as patient_router
from app.modules.patient.service import PatientService
from tests.test_consultation import MemoryRepository, Rag


class PatientStore:
    def __init__(self):
        self.records = {}

    async def list(self):
        return list(self.records.values())

    async def get(self, identifier):
        return deepcopy(self.records.get(identifier))

    async def insert(self, record):
        self.records[record["_id"]] = deepcopy(record)

    async def replace_fields(self, identifier, fields):
        self.records[identifier].update(deepcopy(fields))


def application(rag=None):
    app = FastAPI()
    register_error_handlers(app)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "isolated-http-test"
        return await call_next(request)

    patients = PatientService(PatientStore())
    consultation = ConsultationService(
        MemoryRepository(), patients, rag or Rag(), SimpleNamespace()
    )
    app.state.services = SimpleNamespace(patient=patients, consultation=consultation)
    app.include_router(patient_router, prefix="/api")
    app.include_router(consultation_router, prefix="/api")
    return app


async def test_patient_create_edit_history_and_invalid_payloads_over_http():
    app = application()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post("/api/patients", json={"name": "明确标识的HTTP测试患者"})
        assert response.status_code == 201
        patient = response.json()
        assert patient["birthDate"] is None  # Never fabricate DOB from absent data.
        identifier = patient["id"]
        response = await client.put(
            f"/api/patients/{identifier}",
            json={
                "name": "明确标识的已编辑患者",
                "birthDate": "2000-01-02",
                "allergies": ["测试过敏史"],
            },
        )
        assert response.status_code == 200
        assert response.json()["birthDate"] == "2000-01-02"
        assert len((await client.get("/api/patients")).json()) == 1
        bad = await client.post("/api/patients", json={"name": "", "age": 30})
        assert bad.status_code == 422
        invalid = await client.get("/api/patients/not-an-object-id")
        assert invalid.json()["error"]["code"] == "INVALID_PATIENT_ID"
        conversation = (
            await client.post("/api/conversations", json={"patientId": identifier})
        ).json()
        sent = await client.post(
            f"/api/conversations/{conversation['id']}/messages", json={"content": "HTTP测试"}
        )
        assert sent.status_code == 200
        assert "event: token" in sent.text
        assert "event: done" in sent.text
        history = (await client.get(f"/api/conversations/{conversation['id']}/messages")).json()
        assert [message["status"] for message in history] == ["completed", "completed"]
        assert history[1]["citations"][0]["quote"] == "测试原文"
        rejected = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "test", "attachmentIds": ["file"]},
        )
        assert rejected.status_code == 422
        oversized = await client.post(
            f"/api/conversations/{conversation['id']}/messages", json={"content": "x" * 8001}
        )
        assert oversized.status_code == 422
        assert oversized.json()["error"]["code"] == "MESSAGE_TOO_LONG"
        assert (
            len((await client.get(f"/api/conversations/{conversation['id']}/messages")).json()) == 2
        )


async def test_no_evidence_emits_terminal_error_not_done_over_http():
    class NoEvidence(Rag):
        async def prepare_steps(self, *args):
            # Failure is discovered inside the citation step, after real progress.
            for stage in ("intake", "retrieval"):
                yield "stage", stage
            raise AppError("NO_EVIDENCE", "明确的无证据测试", 422)

    app = application(NoEvidence())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        patient = (await client.post("/api/patients", json={"name": "明确标识的无证据测试"})).json()
        conversation = (
            await client.post("/api/conversations", json={"patientId": patient["id"]})
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages", json={"content": "无证据"}
        )
        assert response.status_code == 200  # Headers precede retrieval; error is SSE.
        assert "event: error" in response.text and "NO_EVIDENCE" in response.text
        assert "event: done" not in response.text
        history = (await client.get(f"/api/conversations/{conversation['id']}/messages")).json()
        assert history[1]["status"] == "failed"
        assert not history[1]["content"]


async def test_asgi_disconnect_cancels_generation_and_retains_partial():
    class PausedRag(Rag):
        async def stream(self, prepared):
            yield "断连前的部分回答"
            await asyncio.Event().wait()

    app = application(PausedRag())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        patient = (await client.post("/api/patients", json={"name": "明确标识的断连测试"})).json()
        conversation = (
            await client.post("/api/conversations", json={"patientId": patient["id"]})
        ).json()
    incoming = asyncio.Queue()
    await incoming.put(
        {
            "type": "http.request",
            "body": json.dumps({"content": "断连"}).encode(),
            "more_body": False,
        }
    )
    frames = []

    async def receive():
        return await incoming.get()

    async def send(message):
        frames.append(message)
        if message["type"] == "http.response.body" and b"event: token" in message.get("body", b""):
            await incoming.put({"type": "http.disconnect"})

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/api/conversations/{conversation['id']}/messages",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=3)
    history = await app.state.services.consultation.history(conversation["id"])
    assert history[1]["status"] == "failed"
    assert history[1]["content"] == "断连前的部分回答"
    assert not app.state.services.consultation.active


async def test_rename_and_delete_conversation_over_http():
    app = application()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        patient = (await client.post("/api/patients", json={"name": "标题接口测试"})).json()
        conversation = (
            await client.post("/api/conversations", json={"patientId": patient["id"]})
        ).json()
        assert conversation["titleSource"] == "default"

        renamed = await client.patch(
            f"/api/conversations/{conversation['id']}/title", json={"title": "手改标题"}
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "手改标题"
        assert renamed.json()["titleSource"] == "user"

        # 全空白会被 schema 归一成空串，必须拒绝而不是存一个空标题。
        blank = await client.patch(
            f"/api/conversations/{conversation['id']}/title", json={"title": "   "}
        )
        assert blank.status_code == 422

        deleted = await client.delete(f"/api/conversations/{conversation['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert (
            await client.get(f"/api/conversations/{conversation['id']}/messages")
        ).status_code == 404
        assert (await client.delete(f"/api/conversations/{conversation['id']}")).status_code == 404
