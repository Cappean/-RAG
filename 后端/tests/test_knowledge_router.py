"""HTTP contracts and admission errors using the actual service and memory queue."""

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.errors import register_error_handlers
from app.modules.knowledge.router import router
from tests.test_knowledge_service import service


def client_for(sut):
    app = FastAPI()
    app.state.settings = sut.settings
    app.state.services = SimpleNamespace(knowledge=sut)

    @app.middleware("http")
    async def request_identifier(request: Request, call_next):
        request.state.request_id = "knowledge-http-test"
        return await call_next(request)

    register_error_handlers(app)
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_upload_and_duplicate_have_real_queue_identity():
    sut = service()
    with client_for(sut) as client:
        response = client.post("/api/knowledge/documents", files={"file": ("test.md", b"text")})
        assert response.status_code == 202
        result = response.json()
        record = sut.queue.get(result["taskId"])
        assert record.kind == "knowledge.ingest"
        assert record.payload == {"documentId": result["document"]["id"]}
        again = client.post("/api/knowledge/documents", files={"file": ("renamed.md", b"text")})
        assert again.status_code == 202
        assert again.json()["duplicate"]
        assert again.json()["taskId"] is None
        assert again.json()["document"] == result["document"]


def test_queue_full_is_http_error_and_document_is_failed():
    sut = service()
    with client_for(sut) as client:
        assert (
            client.post("/api/knowledge/documents", files={"file": ("one.md", b"one")}).status_code
            == 202
        )
        response = client.post("/api/knowledge/documents", files={"file": ("two.md", b"two")})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "TASK_QUEUE_FULL"
        assert response.json()["error"]["requestId"] == "knowledge-http-test"
        assert len(sut.repository.rows) == 2
        assert sum(row["status"] == "failed" for row in sut.repository.rows.values()) == 1


def test_over_limit_read_and_unsigned_original_route():
    sut = service()
    with client_for(sut) as client:
        response = client.post("/api/knowledge/documents", files={"file": ("big.md", b"x" * 2048)})
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert not sut.objects.data
        assert (
            client.get("/api/knowledge/documents/0123456789abcdef01234567/original").status_code
            == 404
        )


def test_limits_endpoint_reports_configured_upload_ceiling():
    sut = service()
    with client_for(sut) as client:
        response = client.get("/api/knowledge/limits")
        assert response.status_code == 200
        assert response.json() == {"uploadMaxBytes": 1024, "uploadMaxLabel": "1 KB"}
