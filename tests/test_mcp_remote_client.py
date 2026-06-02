"""Tests for McpRemoteClient -- SSE/HTTP transports to external MCP servers."""
import pytest
from arf.mcp.remote_client import McpRemoteClient
from arf.core.config_base import McpServerConfig


class TestMcpRemoteClient:
    def test_construct_with_sse_transport(self):
        cfg = McpServerConfig(name="search", transport="sse", url="http://localhost:9000/sse")
        client = McpRemoteClient(cfg)
        assert client.name == "search"
        assert client._transport == "sse"

    def test_construct_with_http_transport(self):
        cfg = McpServerConfig(name="ci", transport="http", url="http://localhost:9001")
        client = McpRemoteClient(cfg)
        assert client._transport == "http"

    def test_tool_name_prefixed(self):
        cfg = McpServerConfig(name="search", url="http://localhost:9000/sse")
        client = McpRemoteClient(cfg)
        assert client._prefix_name("web_search") == "search__web_search"

    def test_strip_prefix(self):
        cfg = McpServerConfig(name="ci", url="http://localhost:9001")
        client = McpRemoteClient(cfg)
        assert client._strip_prefix("ci__python_exec") == "python_exec"
        # Unknown prefix returns unchanged
        assert client._strip_prefix("other__tool") == "other__tool"

    def test_connect_and_disconnect(self):
        import asyncio
        cfg = McpServerConfig(name="test", url="http://localhost:9000/sse")
        client = McpRemoteClient(cfg)
        assert client._connected is False
        async def run():
            await client.connect()
            assert client._connected is True
            await client.disconnect()
            assert client._connected is False
        asyncio.run(run())
