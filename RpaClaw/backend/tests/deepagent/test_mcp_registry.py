import asyncio

from backend.deepagent import mcp_registry
from backend.deepagent.mcp_registry import build_effective_mcp_servers
from backend.mcp.models import McpServerDefinition


class MongoLikeObjectId:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class FakeRepo:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    async def find_many(self, query):
        self.calls.append(query)
        return self.docs


def test_build_effective_mcp_servers_applies_defaults_and_session_overrides(monkeypatch):
    default_enabled_system_server = McpServerDefinition(
        id="pubmed",
        name="PubMed",
        transport="streamable_http",
        scope="system",
        enabled=True,
        default_enabled=True,
        url="http://pubmed/mcp",
    )
    session_enabled_user_server = McpServerDefinition(
        id="private-notion",
        name="Private Notion",
        transport="sse",
        scope="user",
        enabled=True,
        default_enabled=False,
        url="http://notion/mcp",
    )

    monkeypatch.setattr(
        "backend.deepagent.mcp_registry.load_system_mcp_servers",
        lambda: [default_enabled_system_server],
    )

    async def fake_user_servers(user_id: str):
        assert user_id == "u1"
        return [session_enabled_user_server]

    async def fake_bindings(session_id: str, user_id: str):
        return {"user:private-notion": "enabled"}

    monkeypatch.setattr("backend.deepagent.mcp_registry._load_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr("backend.deepagent.mcp_registry._load_session_mcp_bindings", fake_bindings)

    servers = asyncio.run(build_effective_mcp_servers("s1", "u1"))

    assert [server.id for server in servers] == ["pubmed", "private-notion"]


def test_load_user_mcp_servers_stringifies_mongo_like_id(monkeypatch):
    repo = FakeRepo(
        [
            {
                "_id": MongoLikeObjectId("user-mcp-1"),
                "name": "User MCP",
                "transport": "sse",
                "endpoint_config": {"url": "http://user-mcp/mcp"},
            }
        ]
    )
    monkeypatch.setattr(mcp_registry, "get_repository", lambda _: repo)

    servers = asyncio.run(mcp_registry._load_user_mcp_servers("u1"))

    assert repo.calls == [{"user_id": "u1"}]
    assert [server.id for server in servers] == ["user-mcp-1"]


def test_build_effective_mcp_servers_excludes_disabled_user_servers(monkeypatch):
    system_server = McpServerDefinition(
        id="pubmed",
        name="PubMed",
        transport="streamable_http",
        scope="system",
        enabled=True,
        default_enabled=True,
        url="http://pubmed/mcp",
    )
    disabled_user_server = McpServerDefinition(
        id="private-notion",
        name="Private Notion",
        transport="sse",
        scope="user",
        enabled=False,
        default_enabled=False,
        url="http://notion/mcp",
    )

    monkeypatch.setattr(
        "backend.deepagent.mcp_registry.load_system_mcp_servers",
        lambda: [system_server],
    )

    async def fake_user_servers(user_id: str):
        assert user_id == "u1"
        return [disabled_user_server]

    async def fake_bindings(session_id: str, user_id: str):
        return {}

    monkeypatch.setattr("backend.deepagent.mcp_registry._load_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr("backend.deepagent.mcp_registry._load_session_mcp_bindings", fake_bindings)

    servers = asyncio.run(build_effective_mcp_servers("s1", "u1"))

    assert [server.id for server in servers] == ["pubmed"]


def test_build_effective_mcp_servers_excludes_default_enabled_server_when_disabled(monkeypatch):
    disabled_by_binding_system_server = McpServerDefinition(
        id="pubmed",
        name="PubMed",
        transport="streamable_http",
        scope="system",
        enabled=True,
        default_enabled=True,
        url="http://pubmed/mcp",
    )

    monkeypatch.setattr(
        "backend.deepagent.mcp_registry.load_system_mcp_servers",
        lambda: [disabled_by_binding_system_server],
    )
    async def fake_user_servers(user_id: str):
        return []

    monkeypatch.setattr("backend.deepagent.mcp_registry._load_user_mcp_servers", fake_user_servers)

    async def fake_bindings(session_id: str, user_id: str):
        return {"system:pubmed": "disabled"}

    monkeypatch.setattr("backend.deepagent.mcp_registry._load_session_mcp_bindings", fake_bindings)

    servers = asyncio.run(build_effective_mcp_servers("s1", "u1"))

    assert servers == []
