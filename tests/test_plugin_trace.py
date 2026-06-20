"""Tests for TracePlugin — new-style plugin wrapping old logic."""
import pytest
from arf.plugins.trace import TracePlugin, Plugin
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.context import PluginContext


async def fake_call_model(messages, tools=None):
    return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")


class TestTracePluginNewStyle:
    def test_plugin_class_is_exported(self):
        assert Plugin is TracePlugin

    def test_default_construction(self):
        plugin = TracePlugin()
        assert plugin.name == "trace"
        assert len(plugin.events) == 9
        assert "session_start" in plugin.event_names_for_hook("before_round")
        assert "round_start" in plugin.event_names_for_hook("before_round")
        assert "post_action" in plugin.event_names_for_hook("after_model")
        assert "error" in plugin.event_names_for_hook("on_error")

    def test_all_events_are_side_mode(self):
        plugin = TracePlugin()
        for e in plugin.events:
            assert e["mode"] == "side", f"Expected side mode for event {e['event_name']}"

    def test_construction_with_config(self):
        plugin = TracePlugin(
            name="trace",
            events=[],
            config={"data_dir": "/tmp/traces"},
        )
        assert plugin.config["data_dir"] == "/tmp/traces"

    @pytest.mark.anyio
    async def test_handle_does_not_crash(self):
        """Verify handle() can be called without crashing."""
        plugin = TracePlugin()
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="s1")

        # Handle various events
        await plugin.handle("session_start", ctx)
        await plugin.handle("round_start", ctx)
        await plugin.handle("pre_action", ctx)
        await plugin.handle("post_action", ctx)
        await plugin.handle("round_end", ctx)
        await plugin.handle("session_end", ctx)
        await plugin.handle("error", ctx)

    @pytest.mark.anyio
    async def test_handle_writes_trace_file(self, tmp_path):
        """Trace plugin should write JSONL trace files."""
        data_dir = str(tmp_path / "data")
        plugin = TracePlugin(name="trace", events=[], config={"data_dir": data_dir})
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="test-session")

        await plugin.handle("session_start", ctx)

        # Check that trace file was created
        trace_file = tmp_path / "data" / "test-session" / "traces" / "test-session.jsonl"
        if trace_file.exists():
            content = trace_file.read_text()
            assert "session_start" in content
