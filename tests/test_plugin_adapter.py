"""Tests for PluginAdapter — old-style plugins on new AgentHarness."""
import pytest
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.context import PluginContext
from arf.harness.adapter import PluginAdapter, HOOK_TO_CHECKPOINT


async def fake_call_model(messages, tools=None):
    return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")


class MockOldPlugin:
    """Simulates old-style plugin with hooks dict."""

    def __init__(self):
        self.name = "mock_old"
        self.hooks = {"pre_action": "blocking", "post_action": "side"}
        self.calls: list[str] = []

    async def on_pre_action(self, ctx):
        self.calls.append("pre_action")

    async def on_post_action(self, ctx):
        self.calls.append("post_action")


class MockOldPluginWithFire:
    """Old-style plugin using fire() instead of on_<hook> handlers."""

    def __init__(self):
        self.name = "fire_plugin"
        self.hooks = {"round_start": "side"}
        self.calls: list[tuple[str, str]] = []

    async def fire(self, event_name: str, ctx):
        self.calls.append((event_name, ctx.session_id))


class TestPluginAdapter:
    def test_hook_mapping(self):
        assert HOOK_TO_CHECKPOINT["pre_action"] == "before_model"
        assert HOOK_TO_CHECKPOINT["post_action"] == "after_model"
        assert HOOK_TO_CHECKPOINT["round_end"] == "after_round"
        assert HOOK_TO_CHECKPOINT["error"] == "on_error"
        assert HOOK_TO_CHECKPOINT["tool_output"] == "after_tools"

    @pytest.mark.anyio
    async def test_adapter_maps_events(self):
        old = MockOldPlugin()
        adapter = PluginAdapter(old)

        assert adapter.name == "mock_old"
        assert len(adapter.events) == 2

        before_events = adapter.event_names_for_hook("before_model")
        assert "pre_action" in before_events

        after_events = adapter.event_names_for_hook("after_model")
        assert "post_action" in after_events

    @pytest.mark.anyio
    async def test_adapter_delegates_to_handler(self):
        old = MockOldPlugin()
        adapter = PluginAdapter(old)

        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="s1")

        await adapter.handle("pre_action", ctx)
        assert "pre_action" in old.calls

        await adapter.handle("post_action", ctx)
        assert "post_action" in old.calls

    @pytest.mark.anyio
    async def test_adapter_falls_back_to_fire(self):
        old = MockOldPluginWithFire()
        adapter = PluginAdapter(old)

        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        ctx = PluginContext(agent=agent, session_id="s1")

        await adapter.handle("round_start", ctx)
        assert old.calls == [("round_start", "s1")]

    @pytest.mark.anyio
    async def test_adapter_sets_old_context_defaults(self):
        old = MockOldPlugin()
        adapter = PluginAdapter(old)

        agent = PrimitiveAgent("a1",
            model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
            call_model=fake_call_model)
        agent.input("user", "test")
        ctx = PluginContext(agent=agent, session_id="s1")

        await adapter.handle("pre_action", ctx)

        # Old plugin has access to state, messages, session_id via hook_data
        assert ctx.hook_data.get("session_id") == "s1"
        assert ctx.hook_data.get("messages") is agent.state.messages
        assert ctx.hook_data.get("state") is agent.state
