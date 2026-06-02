"""Tests for McpClientManager -- Agent-side MCP client."""
import pytest
from pathlib import Path
from arf.mcp.client_manager import McpClientManager


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
        import asyncio

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
