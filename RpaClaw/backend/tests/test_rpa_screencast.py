import sys
import importlib
import unittest
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SCREencAST_MODULE = importlib.import_module("backend.rpa.screencast")


class _FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


class _FakeCDPSession:
    def __init__(self):
        self.handlers = {}
        self.sent = []
        self.detached = False

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    async def send(self, method, params):
        self.sent.append((method, params))

    async def detach(self):
        self.detached = True


class _FakeContext:
    def __init__(self, failures_before_success=0):
        self.failures_before_success = failures_before_success
        self.calls = 0

    async def new_cdp_session(self, _page):
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError("attach failed")
        return _FakeCDPSession()


class _FakePage:
    def __init__(self, context):
        self.context = context


class _FakeUpstream(AbstractAsyncContextManager):
    def __init__(self):
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(message)


class SessionScreencastControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_page_retries_once_before_succeeding(self):
        context = _FakeContext(failures_before_success=1)
        page = _FakePage(context)
        ws = _FakeWebSocket()
        controller = SCREencAST_MODULE.SessionScreencastController(
            page_provider=lambda: page,
            tabs_provider=lambda: [],
        )
        controller._ws = ws
        controller._running = True

        await controller._switch_page_if_needed(force=True)

        self.assertEqual(context.calls, 2)
        self.assertIsNotNone(controller._cdp)
        self.assertEqual([message["type"] for message in ws.messages], [])

    async def test_switch_page_emits_preview_error_after_retries_fail(self):
        context = _FakeContext(failures_before_success=3)
        page = _FakePage(context)
        ws = _FakeWebSocket()
        controller = SCREencAST_MODULE.SessionScreencastController(
            page_provider=lambda: page,
            tabs_provider=lambda: [],
        )
        controller._ws = ws
        controller._running = True

        with self.assertRaises(RuntimeError):
            await controller._switch_page_if_needed(force=True)

        self.assertEqual(context.calls, 2)
        self.assertEqual(ws.messages[-1]["type"], "preview_error")


class RouteScreencastSelectionTests(unittest.TestCase):
    def test_engine_screencast_url_preserves_base_path_prefix(self):
        import backend.route.rpa as route_rpa

        with patch.object(route_rpa.settings, "rpa_engine_base_url", "http://127.0.0.1:3310/rpa-engine/api"):
            self.assertEqual(
                route_rpa._get_engine_screencast_ws_url("session-1"),
                "ws://127.0.0.1:3310/rpa-engine/api/sessions/session-1/screencast",
            )

    def test_node_mode_screencast_route_uses_proxy_websocket(self):
        import backend.route.rpa as route_rpa

        calls = []
        upstream = _FakeUpstream()
        app = FastAPI()
        app.include_router(route_rpa.router, prefix="/api/v1/rpa")

        async def fake_get_ws_user(_websocket):
            return route_rpa.User(id="user-1", username="tester", role="admin")

        async def fake_get_session(_session_id: str):
            return SimpleNamespace(id="session-1", user_id="user-1", active_tab_id="page-1")

        def fake_connect(url, **kwargs):
            calls.append({"url": url, "kwargs": kwargs})
            return upstream

        with patch.object(route_rpa, "_get_ws_user", fake_get_ws_user), patch.object(
            route_rpa, "rpa_manager", SimpleNamespace(get_session=fake_get_session)
        ), patch.object(route_rpa.settings, "rpa_engine_mode", "node"), patch.object(
            route_rpa.settings, "rpa_engine_base_url", "http://127.0.0.1:3310"
        ), patch.object(
            route_rpa.websockets, "connect", fake_connect
        ), patch.object(
            route_rpa, "SessionScreencastController", side_effect=AssertionError("legacy screencast should not run")
        ):
            client = TestClient(app)
            with client.websocket_connect("/api/v1/rpa/screencast/session-1"):
                pass

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "ws://127.0.0.1:3310/sessions/session-1/screencast")


if __name__ == "__main__":
    unittest.main()
