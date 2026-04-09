import asyncio
from pathlib import Path

import httpx


def build_spawn_command(engine_root: str) -> list[str]:
    return ["npm", "--prefix", engine_root, "run", "dev"]


class LocalRPAEngineSupervisor:
    def __init__(
        self,
        *,
        engine_root: str,
        health_url: str,
        spawn_command: list[str] | None = None,
    ) -> None:
        self._engine_root = Path(engine_root)
        self._health_url = health_url.rstrip("/")
        self._spawn_command = spawn_command or build_spawn_command(str(self._engine_root))
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
        if self._process is not None and self._process.returncode is None:
            return

        self._process = await asyncio.create_subprocess_exec(
            *self._spawn_command,
            cwd=str(self._engine_root),
        )
