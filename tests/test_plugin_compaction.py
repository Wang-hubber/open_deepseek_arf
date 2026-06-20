"""Tests for deeply ported CompactionPlugin."""
import pytest
from arf.plugins.compaction import CompactionPlugin, Plugin
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult, Message
from arf.harness.context import PluginContext


async def fake_call_model(messages, tools=None):
    return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")


class TestCompactionPluginDeep:
    def test_plugin_is_direct_subclass(self):
        from arf.harness.plugin_base import Plugin as BasePlugin
        assert issubclass(CompactionPlugin, BasePlugin)
        # No PluginAdapter indirection
        assert not hasattr(CompactionPlugin, '_adapter')

    def test_events(self):
        p = CompactionPlugin()
        assert p.event_names_for_hook("before_model") == ["compact"]
        assert p.event_names_for_hook("after_tools") == ["safeguard"]

    @pytest.mark.anyio
    async def test_compact_keeps_recent_messages(self, tmp_path):
        """Compaction should keep recent messages when over threshold."""
        plugin = CompactionPlugin(config={"threshold": 10, "keep_recent": 3, "preview_chars": 50})
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)

        # Create many messages
        for i in range(20):
            agent.input("user", f"message {i}")
            agent.input("assistant", f"reply {i}")

        assert len(agent.state.messages) == 40

        ctx = PluginContext(agent=agent, session_id="test", data_dir=str(tmp_path))
        ctx.turn = 1

        await plugin.handle("compact", ctx)

        # Should have kept at most keep_recent*2 = 6 non-tool messages
        kept = [m for m in agent.state.messages if m.role in ("user", "assistant")]
        assert len(kept) <= 6

    @pytest.mark.anyio
    async def test_compact_below_threshold_no_op(self):
        """Compaction should not modify messages when below threshold."""
        plugin = CompactionPlugin(config={"threshold": 500, "keep_recent": 10})
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        agent.input("user", "hello")
        agent.input("assistant", "hi there")

        original_count = len(agent.state.messages)
        ctx = PluginContext(agent=agent, session_id="test")
        await plugin.handle("compact", ctx)

        assert len(agent.state.messages) == original_count

    @pytest.mark.anyio
    async def test_compact_with_tool_messages(self, tmp_path):
        """Compaction should handle tool messages."""
        plugin = CompactionPlugin(config={"threshold": 10, "keep_recent": 2, "preview_chars": 50})
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)

        for i in range(10):
            agent.input("user", f"q{i}")
            agent.input("assistant", {"content": "", "tool_calls": [{"id": f"t{i}", "name": "echo", "params": {}}]})
            agent.input("tool", {"tool_call_id": f"t{i}", "name": "echo", "result": "x" * 100})

        ctx = PluginContext(agent=agent, session_id="test", data_dir=str(tmp_path))
        ctx.turn = 1
        await plugin.handle("compact", ctx)

        # Messages should be compacted
        non_tool = [m for m in agent.state.messages if m.role != "tool"]
        assert len(non_tool) <= 4  # keep_recent=2 means 2 user + 2 assistant max


class TestCompactionSafeguard:
    @pytest.mark.anyio
    async def test_safeguard_handles_empty(self):
        plugin = CompactionPlugin()
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="test")
        # Should not crash with empty hook_data
        await plugin.handle("safeguard", ctx)
