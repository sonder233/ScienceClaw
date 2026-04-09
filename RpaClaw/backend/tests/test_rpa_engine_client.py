import asyncio
import sys
from pathlib import Path

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.rpa.engine_client import RPAEngineClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | Exception):
        self._response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_health_check_raises_on_503(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=503))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="rpa engine health check failed"):
        asyncio.run(client.health())


def test_health_check_raises_on_transport_error(monkeypatch):
    fake_client = _FakeAsyncClient(httpx.ConnectError("boom"))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="rpa engine health check failed"):
        asyncio.run(client.health())


def test_health_check_returns_payload_and_sends_auth_header(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=200, payload={"status": "ok"}))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="secret")

    response = asyncio.run(client.health())

    assert response.status == "ok"
    assert fake_client.calls == [
        ("http://127.0.0.1:3310/health", {"Authorization": "Bearer secret"})
    ]
