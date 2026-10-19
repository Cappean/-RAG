import pytest
from httpx import ASGITransport, AsyncClient

from medical_consultation_backend.main import app, create_app


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
