"""Tests for McpClientManager — Agent-side MCP client."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

import pytest

from arf.mcp.client_manager import McpClientManager
from arf.mcp.protocol import StdioFraming
from arf.core.config_base import McpServerConfig


class TestMcpClientManager:
    def test_init(self, tmp_path):
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        models_dir = tmp_path / "models"
        plugins_dir = tmp_path / "plugins"
        for d in [tools_dir, skills_dir, models_dir, plugins_dir]:
            d.mkdir()

        mgr = McpClientManager(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=models_dir,
            plugins_dir=plugins_dir,
            mcp_servers=[],
            plugin_names=[],
        )
        assert mgr._tools_dir == tools_dir
        assert mgr._started is False

    def test_start_and_stop_no_remote(self, tmp_path):
        """start()/stop() are no-ops when mcp_servers is empty."""
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        models_dir = tmp_path / "models"
        plugins_dir = tmp_path / "plugins"
        for d in [tools_dir, skills_dir, models_dir, plugins_dir]:
            d.mkdir()

        mgr = McpClientManager(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=models_dir,
            plugins_dir=plugins_dir,
            mcp_servers=[],
            plugin_names=[],
        )

        async def run():
            await mgr.start()
            assert mgr._started is True
            assert mgr._remote_started is False  # no remote servers
            await mgr.stop()
            assert mgr._started is False

        asyncio.run(run())

    def test_get_tool_definitions_sync_returns_list(self, tmp_path):
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        models_dir = tmp_path / "models"
        plugins_dir = tmp_path / "plugins"
        for d in [tools_dir, skills_dir, models_dir, plugins_dir]:
            d.mkdir()

        mgr = McpClientManager(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=models_dir,
            plugins_dir=plugins_dir,
            mcp_servers=[],
            plugin_names=[],
        )
        tools = mgr.get_tool_definitions_sync()
        assert isinstance(tools, list)
        # Tools are now plain dicts, not ToolDefinition objects

    def test_list_local_tools(self, tmp_path):
        """Local tools are listed in-process without a subprocess."""
        import yaml
        tool_dir = tmp_path / "tools" / "greet"
        tool_dir.mkdir(parents=True)
        (tool_dir / "tool.yaml").write_text(yaml.dump({
            "name": "greet",
            "description": "Say hello",
            "parameters": {"type": "object", "properties": {}},
        }), encoding="utf-8")
        (tool_dir / "function.py").write_text(
            "async def execute(**kwargs):\n    return {'ok': True}\n"
        )
        for d in [tmp_path / "skills", tmp_path / "models", tmp_path / "plugins"]:
            d.mkdir()

        mgr = McpClientManager(
            tools_dir=tmp_path / "tools",
            skills_dir=tmp_path / "skills",
            models_dir=tmp_path / "models",
            plugins_dir=tmp_path / "plugins",
            mcp_servers=[],
            plugin_names=[],
        )
        tools = mgr._list_local_tools()
        names = [t["name"] for t in tools]
        assert "user__greet" in names


class TestRemoteSend:
    """P1: _remote_send() — id correlation, serialization, stale responses."""

    @pytest.fixture
    def mgr(self, tmp_path):
        for d in [tmp_path / "tools", tmp_path / "skills",
                   tmp_path / "models", tmp_path / "plugins"]:
            d.mkdir()

        return McpClientManager(
            tools_dir=tmp_path / "tools",
            skills_dir=tmp_path / "skills",
            models_dir=tmp_path / "models",
            plugins_dir=tmp_path / "plugins",
            mcp_servers=[McpServerConfig(
                name="test_remote",
                transport="sse",
                url="http://localhost:9000/sse",
            )],
            plugin_names=[],
        )

    @staticmethod
    def _make_response(req_id: int, result: dict) -> bytes:
        resp = json.dumps({
            "jsonrpc": "2.0", "id": req_id, "result": result,
        })
        return StdioFraming.encode(resp)

    @staticmethod
    def _setup_mocks(mgr, read_chunks: list[bytes]):
        idx = [0]
        mock_stdout = MagicMock()

        async def mock_read(_n: int) -> bytes:
            if idx[0] < len(read_chunks):
                chunk = read_chunks[idx[0]]
                idx[0] += 1
                return chunk
            return b""

        mock_stdout.read = mock_read
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = mock_stdout

        mgr._process = mock_process
        mgr._remote_healthy = True
        mgr._remote_started = True
        mgr._started = True
        return idx

    @pytest.mark.anyio
    async def test_stale_response_discarded(self, mgr):
        self._setup_mocks(mgr, [
            self._make_response(99, {"stale": True}),
            self._make_response(1, {"real": True}),
        ])

        result = await mgr._remote_send("tools/call", {"name": "real_tool"})
        assert result == {"real": True}

    @pytest.mark.anyio
    async def test_concurrent_requests_serialized_by_lock(self, mgr):
        self._setup_mocks(mgr, [
            self._make_response(1, {"tool": "first"}),
            self._make_response(2, {"tool": "second"}),
        ])

        async def req_a():
            return await mgr._remote_send("tools/call", {"name": "first"})
        async def req_b():
            return await mgr._remote_send("tools/call", {"name": "second"})

        result_a, result_b = await asyncio.gather(req_a(), req_b())
        assert result_a == {"tool": "first"}
        assert result_b == {"tool": "second"}

    @pytest.mark.anyio
    async def test_out_of_order_responses_in_single_chunk(self, mgr):
        combined = (
            self._make_response(99, {"wrong": True})
            + self._make_response(1, {"correct": True})
        )
        self._setup_mocks(mgr, [combined])

        result = await mgr._remote_send("tools/call", {"name": "correct"})
        assert result == {"correct": True}
