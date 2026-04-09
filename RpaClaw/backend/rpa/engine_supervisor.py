import asyncio
import os
import shlex
import time
from pathlib import Path

import httpx


def build_spawn_command(engine_root: str, start_cmd: str | None = None) -> list[str]:
    if start_cmd:
        return shlex.split(start_cmd, posix=os.name != "nt")
    return ["npm", "run", "dev"]


class LocalRPAEngineSupervisor:
    def __init__(
        self,
        *,
        engine_root: str,
        health_url: str,
        start_cmd: str | None = None,
        spawn_command: list[str] | None = None,
        ready_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self._engine_root = Path(engine_root)
        self._health_url = health_url.rstrip("/")
        self._spawn_command = spawn_command or build_spawn_command(str(self._engine_root), start_cmd)
        self._ready_timeout_seconds = ready_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._process: asyncio.subprocess.Process | None = None

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    async def healthcheck(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(self._health_url)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def ensure_running(self) -> None:
        if await self.healthcheck():
            return
        if self._process is None or self._process.returncode is not None:
            self._process = await asyncio.create_subprocess_exec(
                *self._spawn_command,
                cwd=str(self._engine_root),
            )
        try:
            await self._wait_until_ready()
        except RuntimeError:
            await self._cleanup_process()
            raise

    async def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self._ready_timeout_seconds
        while True:
            if await self.healthcheck():
                return
            if self._process is not None and self._process.returncode is not None:
                raise RuntimeError("rpa engine failed to start")
            if time.monotonic() >= deadline:
                raise RuntimeError("rpa engine did not become ready in time")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _cleanup_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return

        if hasattr(process, "terminate"):
            process.terminate()

        wait = getattr(process, "wait", None)
        if wait is not None:
            try:
                await wait()
            except Exception:
                pass
