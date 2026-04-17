from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.route import mcp as mcp_route


class _User:
    id = "user-1"


def _build_app():
    app = FastAPI()
    app.include_router(mcp_route.router, prefix="/api/v1")
    app.dependency_overrides[mcp_route.require_user] = lambda: _User()
    return app


def test_list_mcp_servers_returns_system_and_user_entries(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [],
    )

    async def fake_user_servers(user_id: str):
        return []

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)

    response = client.get("/api/v1/mcp/servers")

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_create_mcp_server_rejects_stdio_outside_local(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    monkeypatch.setattr(mcp_route.settings, "storage_backend", "docker")

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Local Python MCP",
            "transport": "stdio",
            "endpoint_config": {"command": "python", "args": ["-m", "demo"]},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "stdio MCP is only allowed in local mode"
