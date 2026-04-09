import copy
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import backend.route.rpa as rpa_route
from backend.rpa.manager import RPASessionManager
from backend.user.dependencies import User


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(rpa_route.router, prefix="/api/v1/rpa")
    app.dependency_overrides[rpa_route.get_current_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    return TestClient(app)


def _engine_session_payload() -> dict:
    return {
        "id": "session-1",
        "userId": "user-1",
        "sandboxSessionId": "sandbox-1",
        "status": "recording",
        "activePageAlias": "page-2",
        "pages": [
            {
                "alias": "page-1",
                "title": "Search",
                "url": "https://example.com",
            },
            {
                "alias": "page-2",
                "title": "Popup",
                "url": "https://example.com/popup",
                "openerPageAlias": "page-1",
            },
        ],
        "actions": [
            {
                "id": "action-1",
                "kind": "click",
                "pageAlias": "page-1",
                "framePath": ["iframe[name='editor']"],
                "locator": {
                    "selector": 'internal:role=button[name="Save"]',
                    "locatorAst": {"kind": "role", "role": "button", "name": "Save"},
                },
                "locatorAlternatives": [
                    {
                        "selector": 'internal:role=button[name="Save"]',
                        "locatorAst": {"kind": "role", "role": "button", "name": "Save"},
                        "score": 100,
                        "matchCount": 1,
                        "visibleMatchCount": 1,
                        "isSelected": True,
                        "engine": "playwright",
                        "reason": "strict unique role match",
                    },
                    {
                        "selector": 'internal:testid=[data-testid="save-button"]',
                        "locatorAst": {"kind": "testId", "value": "save-button"},
                        "score": 1,
                        "matchCount": 1,
                        "visibleMatchCount": 1,
                        "isSelected": False,
                        "engine": "playwright",
                        "reason": "stable test id",
                    },
                ],
                "validation": {"status": "ok"},
                "signals": {"popup": {"targetPageAlias": "page-2"}},
            }
        ],
    }


def test_start_session_uses_engine_mode(monkeypatch):
    manager = RPASessionManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    async def fake_start_engine_session(*args, **kwargs):
        return {"id": "session-1", "steps": [], "status": "recording"}

    async def fail_legacy_start_session(*args, **kwargs):
        raise RuntimeError("legacy _start_legacy_session should not be used")

    monkeypatch.setattr(manager, "_start_engine_session", fake_start_engine_session, raising=False)
    monkeypatch.setattr(manager, "_start_legacy_session", fail_legacy_start_session, raising=False)

    client = _build_client()
    response = client.post("/api/v1/rpa/session/start", json={"sandbox_session_id": "sandbox-1"})

    assert response.status_code == 200
    assert response.json()["session"]["id"] == "session-1"


def test_tabs_route_uses_engine_compat_mapping(monkeypatch):
    manager = RPASessionManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    async def fake_fetch_engine_session(session_id: str):
        assert session_id == "session-1"
        return _engine_session_payload()

    monkeypatch.setattr(manager, "_fetch_engine_session", fake_fetch_engine_session, raising=False)

    client = _build_client()
    response = client.get("/api/v1/rpa/session/session-1/tabs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_tab_id"] == "page-2"
    assert payload["tabs"] == [
        {
            "tab_id": "page-1",
            "title": "Search",
            "url": "https://example.com",
            "opener_tab_id": None,
            "status": "open",
            "active": False,
        },
        {
            "tab_id": "page-2",
            "title": "Popup",
            "url": "https://example.com/popup",
            "opener_tab_id": "page-1",
            "status": "open",
            "active": True,
        },
    ]


def test_promote_locator_route_uses_engine_compat_step(monkeypatch):
    manager = RPASessionManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    async def fake_fetch_engine_session(session_id: str):
        assert session_id == "session-1"
        return _engine_session_payload()

    monkeypatch.setattr(manager, "_fetch_engine_session", fake_fetch_engine_session, raising=False)

    client = _build_client()
    response = client.post(
        "/api/v1/rpa/session/session-1/step/0/locator",
        json={"candidate_index": 1},
    )

    assert response.status_code == 200
    step = response.json()["step"]
    assert step["target"] == 'internal:testid=[data-testid="save-button"]'
    assert step["locator_candidates"][0]["selected"] is False
    assert step["locator_candidates"][1]["selected"] is True


def test_promoted_locator_survives_followup_session_fetch(monkeypatch):
    manager = RPASessionManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    engine_session = _engine_session_payload()

    async def fake_fetch_engine_session(session_id: str):
        assert session_id == "session-1"
        return copy.deepcopy(engine_session)

    monkeypatch.setattr(manager, "_fetch_engine_session", fake_fetch_engine_session, raising=False)

    client = _build_client()
    promote_response = client.post(
        "/api/v1/rpa/session/session-1/step/0/locator",
        json={"candidate_index": 1},
    )

    assert promote_response.status_code == 200

    session_response = client.get("/api/v1/rpa/session/session-1")

    assert session_response.status_code == 200
    step = session_response.json()["session"]["steps"][0]
    assert step["target"] == 'internal:testid=[data-testid="save-button"]'
    assert step["locator_candidates"][0]["selected"] is False
    assert step["locator_candidates"][1]["selected"] is True


def test_activate_tab_route_uses_node_mode_compat_state(monkeypatch):
    manager = RPASessionManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    engine_session = _engine_session_payload()

    async def fake_fetch_engine_session(session_id: str):
        assert session_id == "session-1"
        return copy.deepcopy(engine_session)

    monkeypatch.setattr(manager, "_fetch_engine_session", fake_fetch_engine_session, raising=False)

    client = _build_client()
    activate_response = client.post("/api/v1/rpa/session/session-1/tabs/page-1/activate")

    assert activate_response.status_code == 200
    payload = activate_response.json()
    assert payload["active_tab_id"] == "page-1"
    assert payload["result"] == {"tab_id": "page-1", "source": "user"}
    assert payload["tabs"][0]["active"] is True
    assert payload["tabs"][1]["active"] is False

    tabs_response = client.get("/api/v1/rpa/session/session-1/tabs")

    assert tabs_response.status_code == 200
    assert tabs_response.json()["active_tab_id"] == "page-1"
    assert tabs_response.json()["tabs"][0]["active"] is True


def test_navigate_route_uses_node_mode_compat_state(monkeypatch):
    manager = RPASessionManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    engine_session = _engine_session_payload()

    async def fake_fetch_engine_session(session_id: str):
        assert session_id == "session-1"
        return copy.deepcopy(engine_session)

    monkeypatch.setattr(manager, "_fetch_engine_session", fake_fetch_engine_session, raising=False)

    client = _build_client()
    navigate_response = client.post(
        "/api/v1/rpa/session/session-1/navigate",
        json={"url": "docs.example.com"},
    )

    assert navigate_response.status_code == 200
    payload = navigate_response.json()
    assert payload["result"] == {"tab_id": "page-2", "url": "https://docs.example.com"}
    assert payload["tabs"][1]["url"] == "https://docs.example.com"
    assert payload["active_tab_id"] == "page-2"

    tabs_response = client.get("/api/v1/rpa/session/session-1/tabs")

    assert tabs_response.status_code == 200
    assert tabs_response.json()["tabs"][1]["url"] == "https://docs.example.com"


def test_stop_route_clears_node_mode_compat_state(monkeypatch):
    manager = RPASessionManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    engine_session = _engine_session_payload()

    async def fake_fetch_engine_session(session_id: str):
        assert session_id == "session-1"
        return copy.deepcopy(engine_session)

    monkeypatch.setattr(manager, "_fetch_engine_session", fake_fetch_engine_session, raising=False)

    client = _build_client()
    promote_response = client.post(
        "/api/v1/rpa/session/session-1/step/0/locator",
        json={"candidate_index": 1},
    )
    assert promote_response.status_code == 200

    stop_response = client.post("/api/v1/rpa/session/session-1/stop")

    assert stop_response.status_code == 200
    assert stop_response.json()["session"]["status"] == "stopped"
    assert "session-1" not in manager.sessions
    assert "session-1" not in manager._compat_tabs
    assert "session-1" not in manager._engine_sessions
    assert "session-1" not in manager._engine_locator_overrides
