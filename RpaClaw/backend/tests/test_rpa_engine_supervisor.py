import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.rpa.engine_supervisor import LocalRPAEngineSupervisor, build_spawn_command
from backend.rpa.session_gateway import RPASessionGateway


def test_settings_default_start_command_matches_engine_root_cwd(monkeypatch):
    monkeypatch.delenv("RPA_ENGINE_START_CMD", raising=False)

    settings = Settings()

    assert settings.rpa_engine_start_cmd == "npm run dev"


def test_build_spawn_command_honors_explicit_start_command():
    command = build_spawn_command(
        "D:/code/MyScienceClaw/RpaClaw/rpa-engine",
        "node custom-server.mjs --port 3310",
    )

    assert command == ["node", "custom-server.mjs", "--port", "3310"]


def test_build_spawn_command_uses_repo_engine_entrypoint():
    command = build_spawn_command("D:/code/MyScienceClaw/RpaClaw/rpa-engine")

    assert command == ["npm", "run", "dev"]


def test_gateway_accepts_node_mode_and_starts_supervisor():
    client_calls = []
    supervisor_calls = []

    class _FakeClient:
        async def health(self):
            client_calls.append("health")
            return {"status": "ok"}

    class _FakeSupervisor:
        async def ensure_running(self):
            supervisor_calls.append("ensure_running")

    settings = SimpleNamespace(
        rpa_engine_mode="node",
        rpa_engine_base_url="http://127.0.0.1:3310",
        rpa_engine_auth_token="",
        rpa_engine_host="127.0.0.1",
        rpa_engine_port=3310,
        rpa_engine_start_cmd="npm --prefix RpaClaw/rpa-engine run dev",
    )

    gateway = RPASessionGateway(settings=settings, client=_FakeClient(), supervisor=_FakeSupervisor())
    result = asyncio.run(gateway.ensure_engine_ready())

    assert gateway.mode_config.mode == "node"
    assert supervisor_calls == ["ensure_running"]
    assert client_calls == ["health"]
    assert result == {"status": "ok"}


def test_ensure_running_waits_until_health_check_passes(monkeypatch):
    supervisor = LocalRPAEngineSupervisor(
        engine_root="D:/code/MyScienceClaw/RpaClaw/rpa-engine",
        health_url="http://127.0.0.1:3310/health",
        ready_timeout_seconds=1.0,
        poll_interval_seconds=0.01,
    )
    process = SimpleNamespace(returncode=None)
    spawn_calls = []
    sleep_calls = []
    health_states = iter([False, False, True])

    async def fake_create_subprocess_exec(*command, cwd=None):
        spawn_calls.append((list(command), cwd))
        return process

    async def fake_healthcheck():
        return next(health_states)

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr("backend.rpa.engine_supervisor.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(supervisor, "healthcheck", fake_healthcheck)
    monkeypatch.setattr("backend.rpa.engine_supervisor.asyncio.sleep", fake_sleep)

    asyncio.run(supervisor.ensure_running())

    assert len(spawn_calls) == 1
    assert sleep_calls == [0.01]


def test_ensure_running_times_out_when_process_never_becomes_ready(monkeypatch):
    supervisor = LocalRPAEngineSupervisor(
        engine_root="D:/code/MyScienceClaw/RpaClaw/rpa-engine",
        health_url="http://127.0.0.1:3310/health",
        ready_timeout_seconds=0.0,
        poll_interval_seconds=0.01,
    )
    process = SimpleNamespace(returncode=None)

    async def fake_create_subprocess_exec(*command, cwd=None):
        return process

    async def fake_healthcheck():
        return False

    monkeypatch.setattr("backend.rpa.engine_supervisor.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(supervisor, "healthcheck", fake_healthcheck)

    with pytest.raises(RuntimeError, match="rpa engine did not become ready in time"):
        asyncio.run(supervisor.ensure_running())


def test_timeout_resets_bad_process_and_next_call_spawns_again(monkeypatch):
    supervisor = LocalRPAEngineSupervisor(
        engine_root="D:/code/MyScienceClaw/RpaClaw/rpa-engine",
        health_url="http://127.0.0.1:3310/health",
        ready_timeout_seconds=0.0,
        poll_interval_seconds=0.01,
    )
    spawn_calls = []

    class _FakeProcess:
        def __init__(self):
            self.returncode = None
            self.terminate_calls = 0

        def terminate(self):
            self.terminate_calls += 1
            self.returncode = 1

    created_processes = []

    async def fake_create_subprocess_exec(*command, cwd=None):
        spawn_calls.append((list(command), cwd))
        process = _FakeProcess()
        created_processes.append(process)
        return process

    async def fake_healthcheck():
        return False

    monkeypatch.setattr("backend.rpa.engine_supervisor.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(supervisor, "healthcheck", fake_healthcheck)

    with pytest.raises(RuntimeError, match="rpa engine did not become ready in time"):
        asyncio.run(supervisor.ensure_running())

    with pytest.raises(RuntimeError, match="rpa engine did not become ready in time"):
        asyncio.run(supervisor.ensure_running())

    assert len(spawn_calls) == 2
    assert created_processes[0].terminate_calls == 1
