from typing import Any, Literal

from pydantic import BaseModel, Field


class EngineHealthResponse(BaseModel):
    status: str
    details: dict[str, Any] = Field(default_factory=dict)


class EngineSessionRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EngineSessionResponse(BaseModel):
    session_id: str
    status: str
    ws_url: str | None = None
    live_view_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EngineModeConfig(BaseModel):
    mode: Literal["legacy", "remote", "local"] = "legacy"
    base_url: str
    auth_token: str = ""
    host: str = "127.0.0.1"
    port: int = 3310
    start_cmd: str = "npm --prefix RpaClaw/rpa-engine run dev"
