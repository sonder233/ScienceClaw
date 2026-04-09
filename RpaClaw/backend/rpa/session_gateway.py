from backend.config import Settings
from backend.rpa.engine_client import RPAEngineClient
from backend.rpa.engine_models import EngineHealthResponse, EngineModeConfig
from backend.rpa.engine_supervisor import LocalRPAEngineSupervisor


class RPASessionGateway:
    def __init__(
        self,
        settings: Settings,
        client: RPAEngineClient | None = None,
        supervisor: LocalRPAEngineSupervisor | None = None,
    ) -> None:
        self._settings = settings
        self._mode_config = EngineModeConfig(
            mode=settings.rpa_engine_mode,
            base_url=settings.rpa_engine_base_url,
            auth_token=settings.rpa_engine_auth_token,
            host=settings.rpa_engine_host,
            port=settings.rpa_engine_port,
            start_cmd=settings.rpa_engine_start_cmd,
        )
        self._client = client or RPAEngineClient(
            base_url=self._mode_config.base_url,
            auth_token=self._mode_config.auth_token,
        )
        self._supervisor = supervisor

    @property
    def mode_config(self) -> EngineModeConfig:
        return self._mode_config

    async def ensure_engine_ready(self) -> EngineHealthResponse:
        if self._mode_config.mode in {"local", "node"} and self._supervisor is not None:
            await self._supervisor.ensure_running()
        return await self._client.health()
