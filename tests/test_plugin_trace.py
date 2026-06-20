"""Tests for deeply ported TracePlugin."""
import json
import pytest
from pathlib import Path
from arf.plugins.trace import TracePlugin, Plugin
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.context import PluginContext


async def fake_call_model(messages, tools=None):
    return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")


class TestTracePluginDeep:
    def test_plugin_is_direct_subclass(self):
        from arf.harness.plugin_base import Plugin as BasePlugin
        assert issubclass(TracePlugin, BasePlugin)

    def test_events(self):
        p = TracePlugin()
        assert len(p.events) == 9
        assert "session_start" in p.event_names_for_hook("before_round")
        assert "round_start" in p.event_names_for_hook("before_round")
        assert "post_action" in p.event_names_for_hook("after_model")
        assert "error" in p.event_names_for_hook("on_error")

    @pytest.mark.anyio
    async def test_trace_writes_jsonl(self, tmp_path):
        data_dir = str(tmp_path / "data")
        plugin = TracePlugin(config={"data_dir": data_dir})
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="s1", data_dir=data_dir)
        ctx.turn = 1

        await plugin.handle("session_start", ctx)
        await plugin.handle("pre_action", ctx)

        trace_file = Path(data_dir) / "s1" / "traces" / "s1.jsonl"
        assert trace_file.exists()

        lines = trace_file.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            record = json.loads(line)
            assert record["session_id"] == "s1"

    @pytest.mark.anyio
    async def test_read_trace(self, tmp_path):
        data_dir = str(tmp_path / "data")
        plugin = TracePlugin()
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="s2", data_dir=data_dir)

        await plugin.handle("session_start", ctx)

        events = plugin.read_trace("s2", data_dir=data_dir)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"

    @pytest.mark.anyio
    async def test_list_sessions(self, tmp_path):
        data_dir = str(tmp_path / "data")
        plugin = TracePlugin()
        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="s1", data_dir=data_dir)

        await plugin.handle("session_start", ctx)

        sessions = plugin.list_sessions(data_dir=data_dir)
        assert "s1" in sessions
