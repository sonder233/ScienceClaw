from typing import Any, Literal

from pydantic import BaseModel, Field


class EngineHealthResponse(BaseModel):
    status: str
    details: dict[str, Any] = Field(default_factory=dict)


class EngineSessionRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnginePage(BaseModel):
    alias: str
    title: str = ""
    url: str = ""
    openerPageAlias: str | None = None
    status: str = "open"


class EngineSession(BaseModel):
    id: str
    userId: str
    status: str
    sandboxSessionId: str = ""
    activePageAlias: str | None = None
    pages: list[EnginePage] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = "idle"


class EngineSessionEnvelope(BaseModel):
    session: EngineSession


class EngineActivateTabRequest(BaseModel):
    pageAlias: str


class EngineNavigateRequest(BaseModel):
    url: str
    pageAlias: str | None = None


class EngineStartSessionRequest(BaseModel):
    userId: str
    sandboxSessionId: str = ""


class EngineModeConfig(BaseModel):
    mode: Literal["legacy", "remote", "local", "node"] = "legacy"
    base_url: str
    auth_token: str = ""
    host: str = "127.0.0.1"
    port: int = 3310
    start_cmd: str = "npm --prefix RpaClaw/rpa-engine run dev"
