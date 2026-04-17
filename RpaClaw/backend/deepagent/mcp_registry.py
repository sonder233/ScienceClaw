from __future__ import annotations

from typing import Dict, List

from backend.deepagent.mcp_config_loader import load_system_mcp_servers
from backend.mcp.models import McpServerDefinition
from backend.storage import get_repository


async def _load_user_mcp_servers(user_id: str) -> List[McpServerDefinition]:
    repo = get_repository("user_mcp_servers")
    docs = await repo.find_many({"user_id": user_id})
    servers: List[McpServerDefinition] = []
    for doc in docs:
        endpoint = doc.get("endpoint_config") or {}
        servers.append(
            McpServerDefinition(
                id=doc["_id"],
                name=doc["name"],
                description=doc.get("description", ""),
                transport=doc["transport"],
                scope="user",
                enabled=doc.get("enabled", True),
                default_enabled=doc.get("default_enabled", False),
                url=endpoint.get("url", ""),
                command=endpoint.get("command", ""),
                args=endpoint.get("args", []),
                cwd=endpoint.get("cwd", ""),
                headers=endpoint.get("headers", {}),
                env=endpoint.get("env", {}),
                timeout_ms=endpoint.get("timeout_ms", 20000),
                tool_policy=doc.get("tool_policy", {}),
            )
        )
    return servers


async def _load_session_mcp_bindings(session_id: str, user_id: str) -> Dict[str, str]:
    repo = get_repository("session_mcp_bindings")
    docs = await repo.find_many({"session_id": session_id, "user_id": user_id})
    return {doc["server_key"]: doc["mode"] for doc in docs}


async def build_effective_mcp_servers(session_id: str, user_id: str) -> List[McpServerDefinition]:
    merged = load_system_mcp_servers() + await _load_user_mcp_servers(user_id)
    bindings = await _load_session_mcp_bindings(session_id, user_id)
    effective: List[McpServerDefinition] = []

    for server in merged:
        if not server.enabled:
            continue
        key = f"{server.scope}:{server.id}"
        mode = bindings.get(key, "inherit")
        if mode == "disabled":
            continue
        if mode == "enabled" or server.default_enabled:
            effective.append(server)
    return effective
