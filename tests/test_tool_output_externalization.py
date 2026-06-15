"""Tests for tool_output hook externalization in CompactionPlugin."""
import asyncio

from arf.core.plugin_context import PluginContext


class FakeToolOutputPlugin:
    """A fake plugin with a tool_output hook for testing hook modifications."""

    @property
    def name(self) -> str:
        return "fake_tool_output"

    @property
    def hooks(self) -> dict[str, str]:
        return {"tool_output": "blocking"}

    async def on_hook(self, hook_name: str, ctx):
        if hook_name == "tool_output":
            raw = ctx.hook_data.get("_raw_tool_results", {})
            for tc_id, r in raw.items():
                data = r.get("data", "")
                if len(data) > 100:
                    r["data"] = data[:100] + "...[truncated]"


def test_tool_output_hook_modifies_results():
    """Verify a tool_output hook can modify _raw_tool_results data."""
    plugin = FakeToolOutputPlugin()
    ctx = PluginContext(session_id="s1", data_dir="./data")
    ctx.hook_data["_raw_tool_results"] = {
        "call_1": {
            "tool_name": "bash", "success": True,
            "data": "x" * 500, "error": "", "duration_ms": 10.0, "turn": 1,
        }
    }
    asyncio.run(plugin.on_hook("tool_output", ctx))
    result = ctx.hook_data["_raw_tool_results"]["call_1"]["data"]
    assert result.endswith("[truncated]")
    assert len(result) < 500


def test_externalization_skips_read_tools(tmp_path):
    """Read tools (read_file, etc.) should not be externalized."""
    from arf.plugins.compaction.plugin import CompactionPlugin

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    plugin = CompactionPlugin({
        "tool_output": {
            "enabled": True, "threshold": 10,
            "preview_head": 5, "preview_tail": 5, "exclude_tools": [],
        },
        "data_dir": str(data_dir),
    })
    ctx = PluginContext(session_id="s1", data_dir=str(data_dir))
    ctx.hook_data["_raw_tool_results"] = {
        "call_1": {
            "tool_name": "read_file", "success": True,
            "data": "This is a long file content that should not be externalized",
            "error": "", "duration_ms": 5.0, "turn": 1,
        }
    }
    ctx.turn = 1
    asyncio.run(plugin.on_hook("tool_output", ctx))
    result = ctx.hook_data["_raw_tool_results"]["call_1"]["data"]
    assert not result.startswith("[Tool output externalized")
    assert result == "This is a long file content that should not be externalized"


def test_short_results_not_externalized(tmp_path):
    """Results under the threshold should pass through unchanged."""
    from arf.plugins.compaction.plugin import CompactionPlugin

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    plugin = CompactionPlugin({
        "tool_output": {
            "enabled": True, "threshold": 500,
            "preview_head": 50, "preview_tail": 50, "exclude_tools": [],
        },
        "data_dir": str(data_dir),
    })
    ctx = PluginContext(session_id="s1", data_dir=str(data_dir))
    short_result = "short ok"
    ctx.hook_data["_raw_tool_results"] = {
        "call_1": {
            "tool_name": "a2a", "success": True,
            "data": short_result, "error": "", "duration_ms": 5.0, "turn": 1,
        }
    }
    ctx.turn = 1
    asyncio.run(plugin.on_hook("tool_output", ctx))
    result = ctx.hook_data["_raw_tool_results"]["call_1"]["data"]
    assert result == short_result


def test_long_results_externalized_to_disk(tmp_path):
    """Long results should be written to disk and replaced with a preview."""
    from arf.plugins.compaction.plugin import CompactionPlugin

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    plugin = CompactionPlugin({
        "tool_output": {
            "enabled": True, "threshold": 50,
            "preview_head": 20, "preview_tail": 10, "exclude_tools": [],
        },
        "data_dir": str(data_dir),
    })
    ctx = PluginContext(session_id="s1", data_dir=str(data_dir))
    long_result = "A" * 30 + "MIDDLE_CONTENT" + "Z" * 30
    ctx.hook_data["_raw_tool_results"] = {
        "call_1": {
            "tool_name": "a2a", "success": True,
            "data": long_result, "error": "", "duration_ms": 5.0, "turn": 3,
        }
    }
    ctx.turn = 3
    asyncio.run(plugin.on_hook("tool_output", ctx))
    result = ctx.hook_data["_raw_tool_results"]["call_1"]["data"]
    assert "externalized" in result
    assert "full at" in result

    output_dir = data_dir / "s1" / "tool_outputs"
    files = list(output_dir.glob("turn_3_a2a_*.txt"))
    assert len(files) == 1
    assert files[0].read_text() == long_result
