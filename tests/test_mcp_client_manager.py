"""Tests for McpClientManager -- Agent-side MCP client."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from arf.mcp.client_manager import McpClientManager
from arf.mcp.protocol import StdioFraming


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

    def test_start_and_stop(self, tmp_path):
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
            await mgr.stop()
            assert mgr._started is False

        asyncio.run(run())

    def test_get_tool_definitions_sync_returns_list(self, tmp_path):
        """Sync wrapper should return a list (may be empty before start)."""
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


class TestSendRequestIdCorrelation:
    """P1: _send_request() reads any response from stdout, ignoring the
    response id. Concurrent calls can receive each other's responses."""

    @pytest.fixture
    def mgr(self, tmp_path):
        tools_dir = tmp_path / "tools"
        skills_dir = tmp_path / "skills"
        models_dir = tmp_path / "models"
        plugins_dir = tmp_path / "plugins"
        for d in [tools_dir, skills_dir, models_dir, plugins_dir]:
            d.mkdir()

        return McpClientManager(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            models_dir=models_dir,
            plugins_dir=plugins_dir,
            mcp_servers=[],
            plugin_names=[],
        )

    @staticmethod
    def _make_response(req_id: int, result: dict) -> bytes:
        """Build a framed JSON-RPC response for a given request id."""
        resp = json.dumps({
            "jsonrpc": "2.0", "id": req_id, "result": result,
        })
        return StdioFraming.encode(resp)

    @staticmethod
    def _setup_mocks(mgr, read_chunks: list[bytes]):
        """Wire mock stdout/stdin that feeds *read_chunks* in order,
        then returns b"" (EOF).  Each chunk is a single read() result."""
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
        mgr._healthy = True
        mgr._started = True
        return idx

    @pytest.mark.anyio
    async def test_stale_response_discarded(self, mgr):
        """A stale response (wrong id) is discarded; the correct one is found."""
        # _send_request auto-increments _request_id, so the first call
        # sends id=1.  We prepend a stale response with id=99.
        self._setup_mocks(mgr, [
            self._make_response(99, {"stale": True}),
            self._make_response(1, {"real": True}),
        ])

        result = await mgr._send_request("tools/call", {"name": "real_tool"})
        assert result == {"real": True}, (
            f"Got stale response instead of real: {result}"
        )

    @pytest.mark.anyio
    async def test_concurrent_requests_serialized_by_lock(self, mgr):
        """Two concurrent _send_request calls are serialized.  Each
        gets the response matching its own request id."""
        # Prepare two responses in separate reads so each caller
        # reads exactly one chunk.
        self._setup_mocks(mgr, [
            self._make_response(1, {"tool": "first"}),
            self._make_response(2, {"tool": "second"}),
        ])

        async def req_a():
            return await mgr._send_request("tools/call", {"name": "first"})

        async def req_b():
            return await mgr._send_request("tools/call", {"name": "second"})

        result_a, result_b = await asyncio.gather(req_a(), req_b())

        assert result_a == {"tool": "first"}, (
            f"Request A (id=1) got: {result_a}"
        )
        assert result_b == {"tool": "second"}, (
            f"Request B (id=2) got: {result_b}"
        )

    @pytest.mark.anyio
    async def test_out_of_order_responses_in_single_chunk(self, mgr):
        """When two responses arrive in one read() chunk and the first
        has the wrong id, the loop discards it and finds the right one."""
        # _send_request sends id=1, so the correct response must have id=1.
        # The stale id=99 comes first in the combined chunk.
        combined = (
            self._make_response(99, {"wrong": True})
            + self._make_response(1, {"correct": True})
        )
        self._setup_mocks(mgr, [combined])

        result = await mgr._send_request("tools/call", {"name": "correct"})
        assert result == {"correct": True}, (
            f"Expected correct response but got: {result}"
        )
