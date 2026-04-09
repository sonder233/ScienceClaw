import asyncio
import sys
from pathlib import Path

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
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, _url, headers=None):
        return self._response


def test_health_check_raises_on_503(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=503))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="rpa engine health check failed"):
        asyncio.run(client.health())
