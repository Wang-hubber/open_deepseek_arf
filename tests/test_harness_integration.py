"""Full pipeline integration tests — PrimitiveAgent + Harness + Tools + Plugins."""
import pytest
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.harness.engine import AgentHarness
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.tooling.registry import ToolRegistry
from arf.tooling.executor import ToolExecutor
from arf.event_bus import InMemoryEventBus


@pytest.mark.anyio
async def test_full_pipeline_text_only():
    """PrimitiveAgent + Harness → single round, text output."""
    async def fake_call(messages, tools=None):
        return ModelResult(content="Hello, world!", tool_calls=[], usage={"total_tokens": 10}, finish_reason="stop")

    class TestPlugin(Plugin):
        def __init__(self):
            super().__init__("test", [
                {"hook_name": "after_model", "event_name": "after_model_log", "mode": "side"},
            ])
            self.events_received: list[str] = []

        async def handle(self, event_name: str, ctx: PluginContext) -> None:
            self.events_received.append(event_name)

    plugin = TestPlugin()
    agent = PrimitiveAgent("a1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=fake_call)
    bus = InMemoryEventBus()
    harness = AgentHarness(agent, plugins=[plugin], tool_executor=None, event_bus=bus)

    events = [e async for e in harness.run("hello")]
    import asyncio
    await asyncio.sleep(0.05)

    # Agent has 2 messages: user input + assistant response
    assert len(agent.state.messages) == 2
    assert agent.state.messages[0].role == "user"
    assert agent.state.messages[1].role == "assistant"
    assert agent.state.messages[1].content == "Hello, world!"

    # Plugin was called
    assert "after_model_log" in plugin.events_received

    # Events were emitted to event bus
    assert bus.event_count() >= 1
    model_events = bus.collected("model_call_end")
    assert len(model_events) >= 1


@pytest.mark.anyio
async def test_full_pipeline_with_tools():
    """PrimitiveAgent + Harness + ToolExecutor → tool call round trip."""
    turn = 0

    async def fake_call(messages, tools=None):
        nonlocal turn
        turn += 1
        if turn == 1:
            return ModelResult(
                content="",
                tool_calls=[{"id": "t1", "name": "greet", "params": {"name": "World"}}],
                usage={}, finish_reason="tool_calls",
            )
        return ModelResult(content="Greeting sent!", tool_calls=[], usage={}, finish_reason="stop")

    registry = ToolRegistry()

    async def greet(name="", **kw):
        return {"greeting": f"Hello, {name}!"}

    registry.register("greet", {"name": "greet", "description": "Send greeting"}, greet)

    agent = PrimitiveAgent("a1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=fake_call)
    harness = AgentHarness(agent, plugins=[], tool_executor=ToolExecutor(registry))

    events = [e async for e in harness.run("greet World")]

    # Messages: user, assistant(tool_calls), tool_result, assistant(response)
    assert len(agent.state.messages) == 4
    assert agent.state.messages[2].role == "tool"
    assert agent.state.messages[3].content == "Greeting sent!"

    tool_starts = [e for e in events if e.type == "tool_call_start"]
    tool_ends = [e for e in events if e.type == "tool_call_end"]
    assert len(tool_starts) == 1
    assert len(tool_ends) == 1

    # Tool start/end contain correct data
    assert tool_starts[0].data["name"] == "greet"
    assert tool_ends[0].data["name"] == "greet"
    assert tool_ends[0].data["success"] is True


@pytest.mark.anyio
async def test_full_pipeline_multiple_tool_calls():
    """Multiple tool calls in a single turn execute concurrently."""
    turn = 0

    async def fake_call(messages, tools=None):
        nonlocal turn
        turn += 1
        if turn == 1:
            return ModelResult(
                content="",
                tool_calls=[
                    {"id": "t1", "name": "add", "params": {"a": 2, "b": 3}},
                    {"id": "t2", "name": "mul", "params": {"a": 4, "b": 5}},
                ],
                usage={}, finish_reason="tool_calls",
            )
        return ModelResult(content="Results: 5 and 20", tool_calls=[], usage={}, finish_reason="stop")

    registry = ToolRegistry()

    async def add(a=0, b=0, **kw):
        return {"result": a + b}

    async def mul(a=0, b=0, **kw):
        return {"result": a * b}

    registry.register("add", {"name": "add"}, add)
    registry.register("mul", {"name": "mul"}, mul)

    agent = PrimitiveAgent("a1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=fake_call)
    harness = AgentHarness(agent, plugins=[], tool_executor=ToolExecutor(registry))

    events = [e async for e in harness.run("calculate")]

    tool_starts = [e for e in events if e.type == "tool_call_start"]
    tool_ends = [e for e in events if e.type == "tool_call_end"]
    assert len(tool_starts) == 2
    assert len(tool_ends) == 2


@pytest.mark.anyio
async def test_full_pipeline_with_blocking_plugin():
    """Blocking plugin can inject context and wait."""
    async def fake_call(messages, tools=None):
        return ModelResult(content="response with context", tool_calls=[], usage={}, finish_reason="stop")

    class ContextPlugin(Plugin):
        def __init__(self):
            super().__init__("context_injector", [
                {"hook_name": "before_model", "event_name": "inject", "mode": "blocking"},
            ])

        async def handle(self, event_name: str, ctx: PluginContext) -> None:
            ctx.agent.input("system", "You are helpful.")

    agent = PrimitiveAgent("a1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=fake_call)
    harness = AgentHarness(agent, plugins=[ContextPlugin()], tool_executor=None)

    _events = [e async for e in harness.run("hi")]

    # Messages: system(from plugin), user, assistant
    assert len(agent.state.messages) == 3
    assert agent.state.messages[0].role == "user"
    assert any(m.role == "system" for m in agent.state.messages)


@pytest.mark.anyio
async def test_full_pipeline_error_handling():
    """Error during model_call triggers on_error checkpoint and yields error event."""
    async def failing_call(messages, tools=None):
        raise ValueError("API unavailable")

    class ErrorLogger(Plugin):
        def __init__(self):
            super().__init__("error_logger", [
                {"hook_name": "on_error", "event_name": "log_error", "mode": "blocking"},
            ])
            self.errors: list[str] = []

        async def handle(self, event_name: str, ctx: PluginContext) -> None:
            exc = ctx.hook_data.get("exception")
            if exc:
                self.errors.append(str(exc))

    agent = PrimitiveAgent("a1",
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=failing_call)
    logger = ErrorLogger()
    harness = AgentHarness(agent, plugins=[logger])

    events = [e async for e in harness.run("test")]

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 1
    assert "API unavailable" in error_events[0].data["detail"]
    assert len(logger.errors) == 1
