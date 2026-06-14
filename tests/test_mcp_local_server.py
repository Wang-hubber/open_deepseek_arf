"""Tests for ArfLocalMcpServer — remote-only MCP server subprocess."""
import asyncio
import pytest
from pathlib import Path
from arf.mcp.local_server import ArfLocalMcpServer
from arf.core.config_base import McpServerConfig


class TestArfLocalMcpServerConstruction:
    def test_init_empty(self):
        """No remote servers → empty client dict."""
        server = ArfLocalMcpServer(remote_servers=[])
        assert server._remote_clients == {}

    def test_init_with_remote_servers(self):
        remote = [
            McpServerConfig(name="search", transport="sse",
                            url="http://localhost:9000/sse"),
            McpServerConfig(name="data", transport="stdio",
                            command="python", args=["-m", "data_server"]),
        ]
        server = ArfLocalMcpServer(remote_servers=remote)
        assert len(server._remote_clients) == 2
        assert "search" in server._remote_clients
        assert server._remote_clients["search"]._transport == "sse"
        assert "data" in server._remote_clients

    def test_list_tools_sync_empty(self):
        """No remote servers → empty tool list."""
        server = ArfLocalMcpServer(remote_servers=[])
        tools = server.list_tools_sync()
        assert tools == []

    def test_unknown_source_call_tool_returns_error(self):
        server = ArfLocalMcpServer(remote_servers=[])

        async def run():
            result = await server.call_tool("unknown__tool", {})
            assert result["success"] is False
            assert "Unknown remote server" in result["error"]

        asyncio.run(run())
