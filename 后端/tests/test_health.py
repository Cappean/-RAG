import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import app, create_app


@pytest.mark.asyncio
async def test_liveness_and_request_id() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_unknown_route_uses_error_shape() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_ERROR"


async def test_server_error_hides_internals_and_keeps_request_id() -> None:
    test_app = create_app()

    @test_app.get("/fail")
    async def fail():
        raise RuntimeError("private content")

    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/fail", headers={"X-Request-Id": "req_test"})
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req_test"
    assert response.json()["error"]["requestId"] == "req_test"
    assert "private content" not in response.text


async def test_validation_error_does_not_echo_input() -> None:
    test_app = create_app()

    @test_app.get("/validate")
    async def validate(age: int):
        return {"age": age}

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.get("/validate?age=private-content")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "private-content" not in response.text


async def test_lifespan_task_status_and_shutdown() -> None:
    test_app = create_app(Settings(_env_file=None, external_services_enabled=False))
    async with test_app.router.lifespan_context(test_app):
        record = test_app.state.task_queue.submit("demo.noop")
        await test_app.state.task_queue.join()
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/tasks/{record.id}")
            missing = await client.get("/api/tasks/does-not-exist")
        assert response.json()["status"] == "succeeded"
        assert "payload" not in response.json()
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "TASK_NOT_FOUND"
    assert not test_app.state.task_queue.accepting
