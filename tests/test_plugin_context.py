"""Tests for PluginContext."""
from arf.core.plugin_context import PluginContext


class TestInjectEngineEvent:
    def test_inject_appends_to_hook_data(self):
        ctx = PluginContext(session_id="test")
        ctx.inject_engine_event("model_call", {"model": "deepseek", "tokens": 100})
        assert "_engine_events" in ctx.hook_data
        assert len(ctx.hook_data["_engine_events"]) == 1
        evt = ctx.hook_data["_engine_events"][0]
        assert evt["type"] == "model_call"
        assert evt["data"]["model"] == "deepseek"
        assert "timestamp" in evt

    def test_inject_multiple_events_accumulate(self):
        ctx = PluginContext(session_id="test")
        ctx.inject_engine_event("a", {})
        ctx.inject_engine_event("b", {})
        assert len(ctx.hook_data["_engine_events"]) == 2

    def test_hook_data_not_overwritten(self):
        ctx = PluginContext(session_id="test", hook_data={"existing": True})
        ctx.inject_engine_event("e", {})
        assert ctx.hook_data["existing"] is True
        assert "_engine_events" in ctx.hook_data
