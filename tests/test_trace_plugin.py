"""Tests for TracePlugin — hook-mounted flat trace pathway."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from arf.core.plugin_context import PluginContext


class TestTracePlugin:
    @pytest.fixture
    def data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def _make_plugin(self, data_dir):
        from arf.plugins.trace.plugin import TracePlugin
        return TracePlugin({"data_dir": str(data_dir), "enabled": True})

    def test_hooks_declaration(self, data_dir):
        p = self._make_plugin(data_dir)
        hooks = p.hooks
        assert hooks["session_start"] == "side"
        assert hooks["session_end"] == "side"
        assert hooks["round_start"] == "side"
        assert hooks["round_end"] == "side"
        assert hooks["turn_start"] == "side"
        assert hooks["turn_end"] == "side"
        assert hooks["pre_action"] == "side"
        assert hooks["post_action"] == "side"

    def test_on_hook_writes_jsonl(self, data_dir):
        p = self._make_plugin(data_dir)
        ctx = PluginContext(
            session_id="s1",
            interaction_round=1,
            hook_data={"key": "value"},
        )

        async def _run():
            await p.on_hook("round_start", ctx)

        asyncio.run(_run())

        events = p.read_trace("s1")
        assert len(events) == 1
        assert events[0]["type"] == "round_start"
        assert events[0]["round"] == 1
        assert events[0]["data"]["key"] == "value"

    def test_list_sessions(self, data_dir):
        p = self._make_plugin(data_dir)
        ctx1 = PluginContext(session_id="s1")
        ctx2 = PluginContext(session_id="s2")

        async def _run():
            await p.on_hook("round_start", ctx1)
            await p.on_hook("round_start", ctx2)

        asyncio.run(_run())

        sessions = p.list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions

    def test_disabled_plugin_does_nothing(self, data_dir):
        from arf.plugins.trace.plugin import TracePlugin
        p = TracePlugin({"data_dir": str(data_dir), "enabled": False})
        ctx = PluginContext(session_id="s1")

        async def _run():
            await p.on_hook("round_start", ctx)

        asyncio.run(_run())

        assert p.read_trace("s1") == []

    def test_engine_events_flattened(self, data_dir):
        """Engine events from _engine_events are flattened into standalone rows."""
        p = self._make_plugin(data_dir)
        ctx = PluginContext(session_id="s1", interaction_round=1)
        ctx.inject_engine_event("model_call_start", {"model": "gpt4"})
        ctx.inject_engine_event("model_call_end", {
            "model": "gpt4", "content": "hello",
            "tool_calls": [{"name": "read", "params": {"path": "/x"}}],
        })
        ctx.inject_engine_event("tool_call_end", {
            "tool_name": "read", "success": True, "result": "contents",
        })

        async def _run():
            await p.on_hook("post_action", ctx)

        asyncio.run(_run())

        events = p.read_trace("s1")
        types = [e["type"] for e in events]

        # Engine events are flattened before post_action hook event
        assert types == [
            "model_call_start",
            "model_call_end",
            "tool_call_end",
            "post_action",
        ]

        # model_call_end has tool_calls — critical for golden trajectory
        mc = events[1]
        assert mc["data"]["tool_calls"] == [{"name": "read", "params": {"path": "/x"}}]

        # hook event has no _engine_events (popped before write)
        post = events[3]
        assert "_engine_events" not in post["data"]

    def test_read_trace_nonexistent_session(self, data_dir):
        p = self._make_plugin(data_dir)
        assert p.read_trace("nobody") == []

    def test_read_trace_skips_malformed_lines(self, data_dir):
        trace_dir = data_dir / "s1" / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / "s1.jsonl"
        trace_file.write_text(
            '{"type": "ok", "data": {}, "turn": 1, '
            '"timestamp": 1.0, "session_id": "s1"}\nnot json\n',
            encoding="utf-8"
        )
        p = self._make_plugin(data_dir)
        events = p.read_trace("s1")
        assert len(events) == 1
        assert events[0]["type"] == "ok"

    def test_shutdown_is_noop(self, data_dir):
        """shutdown() should be a safe no-op."""
        from arf.plugins.trace.plugin import TracePlugin
        p = TracePlugin({"data_dir": str(data_dir), "enabled": True})

        async def _run():
            await p.shutdown()

        asyncio.run(_run())  # Should not raise

    def test_config_hash_injected_in_events(self, data_dir):
        """Every trace event should contain config_hash field."""
        p = self._make_plugin(data_dir)
        ctx = PluginContext(session_id="s1", interaction_round=1)

        async def _run():
            await p.on_hook("round_start", ctx)

        asyncio.run(_run())

        events = p.read_trace("s1")
        assert len(events) == 1
        assert "config_hash" in events[0]
        assert len(events[0]["config_hash"]) == 12

    def test_config_hash_stable_across_events(self, data_dir):
        """Same session events should share the same config_hash."""
        p = self._make_plugin(data_dir)
        ctx = PluginContext(session_id="s1", interaction_round=1)

        async def _run():
            await p.on_hook("round_start", ctx)
            await p.on_hook("turn_end", ctx)

        asyncio.run(_run())

        events = p.read_trace("s1")
        hashes = {e["config_hash"] for e in events}
        assert len(hashes) == 1  # all the same
