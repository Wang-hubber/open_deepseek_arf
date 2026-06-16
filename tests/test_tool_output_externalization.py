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


