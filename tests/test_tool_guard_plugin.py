"""Tests for ToolGuardPlugin — unified permission + sandbox security."""

import pytest
from arf.plugins.tool_guard.plugin import ToolGuardPlugin, PermissionDenied, SandboxViolation
from arf.core.plugin_context import PluginContext


@pytest.mark.anyio
async def test_deny_list_blocks_tool():
    plugin = ToolGuardPlugin({"deny": ["rm", "shell_exec"]})
    ctx = PluginContext(
        session_id="test",
        state={"_pending_tool_calls": [{"name": "rm", "params": {"path": "file.txt"}}]},
        current_step="execute_tools",
    )

    with pytest.raises(PermissionDenied, match="rm"):
        await plugin.on_hook("pre_dispatch", ctx)


@pytest.mark.anyio
async def test_sandbox_blocks_path_traversal():
    plugin = ToolGuardPlugin({"sandbox_check": True})
    ctx = PluginContext(
        session_id="test",
        state={"_pending_tool_calls": [{"name": "read", "params": {"path": "../../etc/passwd"}}]},
        current_step="execute_tools",
    )

    with pytest.raises(SandboxViolation, match="sandbox violation"):
        await plugin.on_hook("pre_dispatch", ctx)


@pytest.mark.anyio
async def test_allowed_tool_passes():
    plugin = ToolGuardPlugin({})
    ctx = PluginContext(
        session_id="test",
        state={"_pending_tool_calls": [{"name": "read", "params": {"path": "doc.txt"}}]},
        current_step="execute_tools",
    )

    # Should not raise
    await plugin.on_hook("pre_dispatch", ctx)
