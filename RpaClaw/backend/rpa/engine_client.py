import httpx

from backend.rpa.engine_models import EngineHealthResponse


class RPAEngineClient:
    def __init__(self, base_url: str, auth_token: str):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

    async def health(self) -> EngineHealthResponse:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/health", headers=self._headers)
        except httpx.HTTPError as exc:
            raise RuntimeError("rpa engine health check failed") from exc
        if response.status_code != 200:
            raise RuntimeError("rpa engine health check failed")
        return EngineHealthResponse.model_validate(response.json())
