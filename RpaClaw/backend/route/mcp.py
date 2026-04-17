from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
import uuid
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from slugify import slugify
except ImportError:
    def slugify(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized

from backend.config import settings
from backend.deepagent.mcp_config_loader import load_system_mcp_servers
from backend.mcp.models import SessionMcpBindingUpdate, UserMcpServerCreate
from backend.storage import get_repository
from backend.user.dependencies import User, require_user

router = APIRouter(prefix="/mcp", tags=["mcp"])


class ApiResponse(BaseModel):
    code: int = Field(default=0)
    msg: str = Field(default="ok")
    data: Any = Field(default=None)


async def _list_user_mcp_servers(user_id: str) -> List[Dict[str, Any]]:
    repo = get_repository("user_mcp_servers")
    return await repo.find_many({"user_id": user_id}, sort=[("updated_at", -1)])


@router.get("/servers", response_model=ApiResponse)
async def list_mcp_servers(current_user: User = Depends(require_user)) -> ApiResponse:
    system_servers = [
        server.model_dump() | {"readonly": True, "server_key": f"system:{server.id}"}
        for server in load_system_mcp_servers()
    ]
    user_servers = []
    for doc in await _list_user_mcp_servers(str(current_user.id)):
        user_servers.append({**doc, "readonly": False, "server_key": f"user:{doc['_id']}"})
    return ApiResponse(data=system_servers + user_servers)


@router.post("/servers", response_model=ApiResponse)
async def create_mcp_server(
    body: UserMcpServerCreate,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    if body.transport == "stdio" and settings.storage_backend != "local":
        raise HTTPException(status_code=400, detail="stdio MCP is only allowed in local mode")

    repo = get_repository("user_mcp_servers")
    server_id = f"mcp_{uuid.uuid4().hex[:12]}"
    now = datetime.now()
    doc = {
        "_id": server_id,
        "user_id": str(current_user.id),
        "name": body.name,
        "slug": slugify(body.name),
        "description": body.description,
        "transport": body.transport,
        "enabled": True,
        "default_enabled": body.default_enabled,
        "endpoint_config": body.endpoint_config,
        "credential_binding": body.credential_binding.model_dump(),
        "tool_policy": body.tool_policy.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    await repo.insert_one(doc)
    return ApiResponse(data={"id": server_id, "saved": True})


@router.put("/sessions/{session_id}/servers/{server_key}", response_model=ApiResponse)
async def update_session_override(
    session_id: str,
    server_key: str,
    body: SessionMcpBindingUpdate,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    repo = get_repository("session_mcp_bindings")
    await repo.update_one(
        {"session_id": session_id, "user_id": str(current_user.id), "server_key": server_key},
        {"$set": {"mode": body.mode, "updated_at": datetime.now()}},
        upsert=True,
    )
    return ApiResponse(data={"session_id": session_id, "server_key": server_key, "mode": body.mode})
