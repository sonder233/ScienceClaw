from pathlib import Path

import pytest

from backend.deepagent.mcp_config_loader import load_system_mcp_servers


def test_load_system_mcp_servers_reads_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        """
servers:
  - id: pubmed
    name: PubMed MCP
    transport: streamable_http
    enabled: true
    default_enabled: true
    url: http://mcp-pubmed:8080/mcp
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_BACKEND", "docker")

    servers = load_system_mcp_servers(config_file)

    assert len(servers) == 1
    assert servers[0].id == "pubmed"
    assert servers[0].transport == "streamable_http"


def test_load_system_mcp_servers_rejects_stdio_outside_local(tmp_path, monkeypatch):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        """
servers:
  - id: local-python
    name: Local Python MCP
    transport: stdio
    enabled: true
    default_enabled: false
    command: python
    args: ["-m", "demo_mcp"]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_BACKEND", "docker")

    with pytest.raises(ValueError, match="stdio MCP is only allowed in local mode"):
        load_system_mcp_servers(config_file)
