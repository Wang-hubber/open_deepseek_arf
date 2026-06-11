"""Tests for ArfLocalMcpServer — local MCP server subprocess."""
import pytest
from pathlib import Path
from arf.mcp.local_server import ArfLocalMcpServer
from arf.core.config_base import McpServerConfig


class TestArfLocalMcpServerConstruction:
    def test_init_creates_providers(self, tmp_path):
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        tools_dir.mkdir()
        skills_dir.mkdir()

        server = ArfLocalMcpServer(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=tmp_path / "models",
            plugins_dir=tmp_path / "plugins",
            plugin_names=[],
            remote_servers=[],
        )
        assert server._tool_provider is not None
        assert server._skill_provider is not None

    def test_init_with_plugin_names(self, tmp_path):
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        plugins_dir = tmp_path / "plugins"
        tools_dir.mkdir()
        skills_dir.mkdir()
        plugins_dir.mkdir()

        server = ArfLocalMcpServer(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=tmp_path / "models",
            plugins_dir=plugins_dir,
            plugin_names=["planner", "searcher"],
            remote_servers=[],
        )
        assert server._plugin_provider is not None

    def test_init_with_remote_servers(self, tmp_path):
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        tools_dir.mkdir()
        skills_dir.mkdir()

        remote = [
            McpServerConfig(name="search", transport="sse", url="http://localhost:9000/sse"),
        ]
        server = ArfLocalMcpServer(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=tmp_path / "models",
            plugins_dir=tmp_path / "plugins",
            plugin_names=[],
            remote_servers=remote,
        )
        assert "search" in server._remote_clients
        assert server._remote_clients["search"]._transport == "sse"


class TestArfLocalMcpServerToolPrefixing:
    def test_local_tools_prefixed_arf(self, tmp_path):
        import yaml
        tools_dir = tmp_path / "tools" / "bash"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool.yaml").write_text(yaml.dump({
            "name": "bash",
            "description": "Run shell commands",
            "parameters": {"type": "object", "properties": {}},
            "activation": "kernel",
        }), encoding="utf-8")
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        server = ArfLocalMcpServer(
            tools_dir=tmp_path / "tools",
            skills_dir=skills_dir,
            models_dir=tmp_path / "models",
            plugins_dir=tmp_path / "plugins",
            plugin_names=[],
            remote_servers=[],
        )
        tools = server.list_tools_sync()
        names = [t["name"] for t in tools]
        assert "user__bash" in names
        assert "bash" not in names

    def test_unknown_source_call_tool_returns_error(self, tmp_path):
        import asyncio
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        tools_dir.mkdir()
        skills_dir.mkdir()

        server = ArfLocalMcpServer(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=tmp_path / "models",
            plugins_dir=tmp_path / "plugins",
            plugin_names=[],
            remote_servers=[],
        )
        async def run():
            result = await server.call_tool("unknown__tool", {})
            assert result["success"] is False
            assert "Unknown source" in result["error"]
        asyncio.run(run())
