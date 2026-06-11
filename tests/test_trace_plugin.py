"""Tests for TracePlugin — unified trace pathway."""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest

from arf.core.plugin_context import PluginContext
from arf.core.events import AgentEvent
from arf.event_bus import InMemoryEventBus


class TestTracePlugin:
    @pytest.fixture
    def trace_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    @pytest.fixture
    def bus(self):
        return InMemoryEventBus()

    def _make_plugin(self, trace_dir, bus=None):
        from arf.plugins.trace.plugin import TracePlugin
        p = TracePlugin({"trace_dir": str(trace_dir), "enabled": True})
        if bus is not None:
            p.set_event_bus(bus)
        return p

    def test_hooks_declaration(self, trace_dir, bus):
        p = self._make_plugin(trace_dir, bus)
        hooks = p.hooks
        assert hooks["session_start"] == "side"
        assert hooks["session_end"] == "side"
        assert hooks["round_start"] == "side"
        assert hooks["round_end"] == "side"
        assert hooks["turn_start"] == "side"
        assert hooks["turn_end"] == "side"
        assert hooks["pre_action"] == "side"
        assert hooks["post_action"] == "side"

    def test_on_hook_writes_jsonl(self, trace_dir, bus):
        p = self._make_plugin(trace_dir, bus)
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
        assert events[0]["turn"] == 1
        assert events[0]["data"]["key"] == "value"

    def test_list_sessions(self, trace_dir, bus):
        p = self._make_plugin(trace_dir, bus)
        ctx1 = PluginContext(session_id="s1")
        ctx2 = PluginContext(session_id="s2")

        async def _run():
            await p.on_hook("round_start", ctx1)
            await p.on_hook("round_start", ctx2)

        asyncio.run(_run())

        sessions = p.list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions

    def test_disabled_plugin_does_nothing(self, trace_dir, bus):
        from arf.plugins.trace.plugin import TracePlugin
        p = TracePlugin({"trace_dir": str(trace_dir), "enabled": False})
        p.set_event_bus(bus)
        ctx = PluginContext(session_id="s1")

        async def _run():
            await p.on_hook("round_start", ctx)

        asyncio.run(_run())

        assert p.read_trace("s1") == []

    def test_hook_data_engine_events_recorded(self, trace_dir, bus):
        p = self._make_plugin(trace_dir, bus)
        ctx = PluginContext(session_id="s1", interaction_round=1)
        ctx.inject_engine_event("model_call", {"model": "gpt4", "tokens": 100})

        async def _run():
            await p.on_hook("post_action", ctx)

        asyncio.run(_run())

        events = p.read_trace("s1")
        post = [e for e in events if e["type"] == "post_action"]
        assert len(post) == 1

    def test_read_trace_nonexistent_session(self, trace_dir, bus):
        p = self._make_plugin(trace_dir, bus)
        assert p.read_trace("nobody") == []

    def test_read_trace_skips_malformed_lines(self, trace_dir, bus):
        trace_file = trace_dir / "s1.jsonl"
        trace_file.write_text(
            '{"type": "ok", "data": {}, "turn": 1, '
            '"timestamp": 1.0, "session_id": "s1"}\nnot json\n',
            encoding="utf-8"
        )
        p = self._make_plugin(trace_dir, bus)
        events = p.read_trace("s1")
        assert len(events) == 1
        assert events[0]["type"] == "ok"

    @pytest.mark.anyio
    async def test_eventbus_events_recorded(self, trace_dir, bus):
        """EventBus events should be written to JSONL by background task."""
        p = self._make_plugin(trace_dir, bus)

        # Yield control so the consumer task can create its subscription queue
        await asyncio.sleep(0)

        bus.emit(AgentEvent(
            type="model_call_end", turn=1,
            data={"model": "deepseek", "content": "hello"},
            session_id="s1",
        ))
        bus.emit(AgentEvent(
            type="tool_call_end", turn=1,
            data={"tool_name": "read", "success": True},
            session_id="s1",
        ))

        # Give the async subscription task time to consume
        await asyncio.sleep(0.2)

        events = p.read_trace("s1")
        types = [e["type"] for e in events]
        assert "model_call_end" in types
        assert "tool_call_end" in types

    def test_shutdown_cancels_task(self, trace_dir, bus):
        """shutdown() should cancel the background task cleanly."""
        from arf.plugins.trace.plugin import TracePlugin
        p = TracePlugin({"trace_dir": str(trace_dir), "enabled": True})

        async def _run():
            p.set_event_bus(bus)
            assert p._consume_task is not None
            assert not p._consume_task.done()
            await p.shutdown()
            assert p._consume_task is None

        asyncio.run(_run())

    def test_shutdown_safe_when_no_task(self, trace_dir, bus):
        """shutdown() should be safe when no subscription was started."""
        from arf.plugins.trace.plugin import TracePlugin
        p = TracePlugin({"trace_dir": str(trace_dir), "enabled": True})
        # Never called set_event_bus — no consume task
        async def _run():
            await p.shutdown()
        asyncio.run(_run())  # Should not raise
