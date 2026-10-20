"""Gateway security and actual HTTP range boundaries without external network."""

import secrets
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.infrastructure.runtime import ObjectStore
from app.main import create_app


@pytest.mark.asyncio
async def test_signed_object_ranges_and_expiry(monkeypatch):
    # Keep the production signature and verification implementation; replace only storage.
    store = ObjectStore.__new__(ObjectStore)
    store._signing_key = secrets.token_bytes(32)
    store.bucket = "test-bucket"
    store.client = SimpleNamespace(stat_object=lambda *args: None)
    store.get = lambda key: b"%PDF-123456789"
    clock = [1000]
    monkeypatch.setattr("app.infrastructure.runtime.time.time", lambda: clock[0])
    url = store.presign("knowledge/test/original.pdf", 30)
    app = create_app(Settings(_env_file=None, external_services_enabled=False))
    app.state.objects = store
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        full = await c.get(url)
        assert full.status_code == 200 and full.content == b"%PDF-123456789"
        ranged = await c.get(url, headers={"Range": "bytes=0-4"})
        assert ranged.status_code == 206 and ranged.content == b"%PDF-"
        assert ranged.headers["Content-Range"] == "bytes 0-4/14"
        suffix = await c.get(url, headers={"Range": "bytes=-3"})
        assert suffix.content == b"789"
        assert (await c.get(url, headers={"Range": "bytes=99-100"})).status_code == 416
        assert (await c.get(url.split("signature=")[0] + "signature=%E4%B8%AD")).status_code == 403
        clock[0] = 1031
        assert (await c.get(url)).status_code == 403
        assert (await c.get(url.replace("original.pdf", "another.pdf"))).status_code == 403
