"""Tests for arf MCP Python bindings."""
import os
import tempfile
import pytest
from arf import Bus, McpNode, RemoteConfig, RetryConfig


class TestRetryConfig:
    """[构造] RetryConfig — default values and custom values."""

    def test_default_values(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.initial_backoff_ms == 1000
        assert cfg.max_backoff_ms == 30000

    def test_custom_values(self):
        cfg = RetryConfig(max_retries=5, initial_backoff_ms=2000, max_backoff_ms=60000)
        assert cfg.max_retries == 5
        assert cfg.initial_backoff_ms == 2000
        assert cfg.max_backoff_ms == 60000

    def test_repr(self):
        cfg = RetryConfig()
        assert "RetryConfig" in repr(cfg)
        assert "max_retries=3" in repr(cfg)


class TestRemoteConfig:
    """[构造] RemoteConfig — URL + optional fields."""

    def test_minimal(self):
        cfg = RemoteConfig(url="https://example.com/mcp")
        assert cfg.url == "https://example.com/mcp"
        assert cfg.transport == "http"
        assert cfg.timeout_secs is None
        assert cfg.tls_ca_cert is None
        assert cfg.retry is None

    def test_full(self):
        retry = RetryConfig(max_retries=5)
        cfg = RemoteConfig(
            url="https://example.com/mcp",
            transport="http",
            timeout_secs=60,
            headers={"Authorization": "Bearer tok"},
            tls_ca_cert="/path/to/ca.pem",
            retry=retry,
        )
        assert cfg.url == "https://example.com/mcp"
        assert cfg.timeout_secs == 60
        assert cfg.tls_ca_cert == "/path/to/ca.pem"
        assert cfg.retry.max_retries == 5

    def test_repr(self):
        cfg = RemoteConfig(url="https://example.com/mcp")
        assert "RemoteConfig" in repr(cfg)


class TestMcpNodeLocal:
    """[构造][生命周期] McpNode.local() — filesystem scan + connect."""

    def test_local_creates_with_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = tmp
            tool_dir = os.path.join(root, "tools", "hello")
            os.makedirs(tool_dir)
            with open(os.path.join(tool_dir, "tool.toml"), "w") as f:
                f.write(
                    'name = "hello"\n'
                    'description = "Say hello"\n'
                    'runtime = "bash"\n'
                    'entrypoint = "main.sh"\n'
                )
            with open(os.path.join(tool_dir, "main.sh"), "w") as f:
                f.write("#!/bin/bash\nread p\necho '{\"msg\":\"hello\"}'")

            node = McpNode.local("test", root)
            assert node.namespace == "test"
            assert "mcp/test" in node.node_id

    def test_local_missing_root(self):
        with pytest.raises(RuntimeError, match="discovery"):
            McpNode.local("test", "/nonexistent/path/xyz")

    def test_local_empty_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = McpNode.local("test", tmp)
            assert node.namespace == "test"

    def test_repr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = tmp
            tool_dir = os.path.join(root, "tools", "hello")
            os.makedirs(tool_dir)
            with open(os.path.join(tool_dir, "tool.toml"), "w") as f:
                f.write(
                    'name = "hello"\n'
                    'description = "Say hello"\n'
                    'runtime = "bash"\n'
                    'entrypoint = "main.sh"\n'
                )
            with open(os.path.join(tool_dir, "main.sh"), "w") as f:
                f.write("#!/bin/bash\nread p\necho '{\"msg\":\"hello\"}'")

            node = McpNode.local("test", root)
            r = repr(node)
            assert "McpNode" in r

    @pytest.mark.asyncio
    async def test_connect_to_bus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = tmp
            tool_dir = os.path.join(root, "tools", "echo")
            os.makedirs(tool_dir)
            with open(os.path.join(tool_dir, "tool.toml"), "w") as f:
                f.write(
                    'name = "echo"\n'
                    'description = "Echo"\n'
                    'runtime = "bash"\n'
                    'entrypoint = "main.sh"\n'
                )
            with open(os.path.join(tool_dir, "main.sh"), "w") as f:
                f.write("#!/bin/bash\nread p\necho '{\"msg\":\"ok\"}'")

            bus = Bus()
            node = McpNode.local("test", root)
            await node.connect(bus)

            graph = bus.graph()
            mcp_nodes = [n for n in graph.nodes if n.node_type == "mcp"]
            assert len(mcp_nodes) == 1
            assert str(mcp_nodes[0].node_id) == node.node_id

            caps = mcp_nodes[0].capabilities
            assert "runtime" in caps
            assert "tools" in caps
            assert len(caps["tools"]) == 1
            assert caps["tools"][0]["name"] == "echo"

            await bus.shutdown()


class TestMcpNodeRemoteConfig:
    """[构造] RemoteConfig flows — new() doesn't connect."""

    def test_remote_config_roundtrip(self):
        retry = RetryConfig(max_retries=2, initial_backoff_ms=500)
        cfg = RemoteConfig(
            url="https://mcp.example.com",
            timeout_secs=45,
            headers={"X-API-Key": "secret"},
            retry=retry,
        )
        assert cfg.transport == "http"
        assert cfg.url == "https://mcp.example.com"
        assert cfg.timeout_secs == 45
        assert cfg.retry.max_retries == 2
        assert cfg.retry.initial_backoff_ms == 500


class TestMcpNodeRepr:
    """[类型] __repr__ coverage."""

    def test_retry_config_repr(self):
        r = repr(RetryConfig(max_retries=5))
        assert "RetryConfig" in r
        assert "5" in r

    def test_remote_config_repr(self):
        r = repr(RemoteConfig(url="https://x.com"))
        assert "RemoteConfig" in r
        assert "https://x.com" in r
