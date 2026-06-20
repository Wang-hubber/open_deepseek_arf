"""Tests for CompactionPlugin — new-style plugin wrapping old logic."""
import pytest
from arf.plugins.compaction import CompactionPlugin, Plugin
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.context import PluginContext


async def fake_call_model(messages, tools=None):
    return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")


class TestCompactionPluginNewStyle:
    def test_plugin_class_is_exported(self):
        assert Plugin is CompactionPlugin

    def test_default_construction(self):
        plugin = CompactionPlugin()
        assert plugin.name == "compaction"
        assert len(plugin.events) == 2
        assert plugin.event_names_for_hook("before_model") == ["compact"]
        assert plugin.event_names_for_hook("after_tools") == ["tool_output"]

    def test_construction_with_config(self):
        plugin = CompactionPlugin(
            name="compaction",
            events=[{"hook_name": "before_model", "event_name": "compact", "mode": "blocking"}],
            config={"threshold": 0.8, "keep_count": 5},
        )
        assert plugin.config["threshold"] == 0.8
        assert plugin.config["keep_count"] == 5

    def test_mode_is_blocking(self):
        plugin = CompactionPlugin()
        assert plugin.mode_for("before_model", "compact") == "blocking"
        assert plugin.mode_for("after_tools", "tool_output") == "blocking"

    @pytest.mark.anyio
    async def test_handle_does_not_crash(self):
        """Verify handle() can be called without crashing (even if old logic is partially wired)."""
        plugin = CompactionPlugin()
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        agent.input("user", "test")
        ctx = PluginContext(agent=agent, session_id="s1")
        # Should not raise — old plugin's hook handlers wrapped via adapter
        await plugin.handle("compact", ctx)
        await plugin.handle("tool_output", ctx)
