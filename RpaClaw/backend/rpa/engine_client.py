import httpx

from backend.rpa.engine_models import (
    EngineActivateTabRequest,
    EngineHealthResponse,
    EngineNavigateRequest,
    EngineStartSessionRequest,
    EngineSessionEnvelope,
)


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

    async def get_session(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{self._base_url}/sessions/{session_id}",
                headers=self._headers,
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def start_session(self, user_id: str, sandbox_session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions",
                headers=self._headers,
                json=EngineStartSessionRequest(
                    userId=user_id,
                    sandboxSessionId=sandbox_session_id,
                ).model_dump(),
            )
        if response.status_code not in {200, 201}:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def activate_tab(self, session_id: str, page_alias: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/activate",
                headers=self._headers,
                json=EngineActivateTabRequest(pageAlias=page_alias).model_dump(),
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def navigate_session(self, session_id: str, url: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/navigate",
                headers=self._headers,
                json=EngineNavigateRequest(url=url).model_dump(exclude_none=True),
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def stop_session(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/stop",
                headers=self._headers,
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()
