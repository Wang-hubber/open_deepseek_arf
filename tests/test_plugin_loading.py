"""Tests for PluginContext and Plugin base class."""
import pytest
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult


async def fake_call_model(messages, tools=None):
    return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")


def make_agent(agent_id="a1"):
    return PrimitiveAgent(
        agent_id=agent_id,
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=fake_call_model,
    )


class FakePlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="fake",
            events=[
                {"hook_name": "before_model", "event_name": "compact", "mode": "blocking"},
                {"hook_name": "after_model", "event_name": "log", "mode": "side"},
            ],
        )
        self.handled: list[tuple[str, str]] = []

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        self.handled.append((event_name, ctx.session_id))


class TestPlugin:
    def test_event_names_for_hook(self):
        p = FakePlugin()
        assert p.event_names_for_hook("before_model") == ["compact"]
        assert p.event_names_for_hook("after_model") == ["log"]
        assert p.event_names_for_hook("before_tools") == []

    def test_mode_for(self):
        p = FakePlugin()
        assert p.mode_for("before_model", "compact") == "blocking"
        assert p.mode_for("after_model", "log") == "side"

    @pytest.mark.anyio
    async def test_handle_receives_context(self):
        agent = make_agent()
        ctx = PluginContext(agent=agent, session_id="s1")
        p = FakePlugin()
        await p.handle("compact", ctx)
        assert p.handled == [("compact", "s1")]

    def test_plugin_context_emit(self):
        from arf.event_bus import InMemoryEventBus

        agent = make_agent()
        bus = InMemoryEventBus()
        ctx = PluginContext(agent=agent, session_id="s1", event_bus=bus)
        ctx.emit("test_event", {"key": "value"})
        assert bus.event_count() == 1
        assert bus.collected("test_event")[0].data["key"] == "value"
