"""Integration tests for AgentHarness — execution loop + plugins + park/resume."""
import asyncio
import pytest
from arf.agent.config import AgentConfig
from arf.agent.state import ModelResult
from arf.agent.primitive import PrimitiveAgent
from arf.core.config_base import ToolConfig
from arf.harness.engine import AgentHarness
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.event_bus import InMemoryEventBus


class FakeToolResult:
    def __init__(self, success=True, data="ok", error=""):
        self.success = success
        self.data = data
        self.error = error
        self.duration_ms = 10


class FakeToolExecutor:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def get_tool_definitions(self):
        return []

    async def execute(self, name, params):
        self.calls.append((name, params))
        return FakeToolResult()


def make_agent(call_model, agent_id="a1"):
    return PrimitiveAgent(
        agent_id=agent_id,
        model_config={"api_base": "", "api_key_env": "", "model_name": "", "context_window": 0},
        call_model=call_model,
    )


class TestHarnessBasicFlow:
    @pytest.mark.anyio
    async def test_run_text_only_response(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="Hello, user!", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[], tool_manager=None)
        events = [e async for e in harness.run("hi")]

        assert any(e.type == "model_call_end" for e in events)
        model_end = next(e for e in events if e.type == "model_call_end")
        assert model_end.data["content"] == "Hello, user!"
        # Agent has 2 messages: user input + assistant response
        assert len(agent.state.messages) == 2
        assert agent.state.messages[0].role == "user"
        assert agent.state.messages[1].role == "assistant"
        assert agent.state.messages[1].content == "Hello, user!"

    @pytest.mark.anyio
    async def test_run_with_tool_calls(self):
        turn = 0

        async def fake_call(messages, tools=None):
            nonlocal turn
            turn += 1
            if turn == 1:
                return ModelResult(
                    content="",
                    tool_calls=[{"id": "t1", "name": "read_file", "params": {"path": "x.txt"}}],
                    usage={}, finish_reason="tool_calls",
                )
            return ModelResult(content="File contents: hello", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        tool_exec = FakeToolExecutor()
        harness = AgentHarness(agent, plugins=[], tool_manager=tool_exec)

        events = [e async for e in harness.run("read x.txt")]

        assert len(tool_exec.calls) == 1
        assert tool_exec.calls[0][0] == "read_file"
        model_ends = [e for e in events if e.type == "model_call_end"]
        assert len(model_ends) == 2  # tool_calls turn + final response turn
        # Messages: user, assistant(tool_calls), tool_result, assistant(response)
        assert agent.state.messages[2].role == "tool"
        assert agent.state.messages[3].content == "File contents: hello"

    @pytest.mark.anyio
    async def test_run_respects_max_turns(self):
        call_count = 0

        async def infinite_tools(messages, tools=None):
            nonlocal call_count
            call_count += 1
            return ModelResult(
                content="",
                tool_calls=[{"id": "t1", "name": "echo", "params": {}}],
                usage={}, finish_reason="tool_calls",
            )

        agent = make_agent(infinite_tools)
        tool_exec = FakeToolExecutor()
        harness = AgentHarness(agent, plugins=[], tool_manager=tool_exec, max_turns=3)

        events = [e async for e in harness.run("loop")]
        model_ends = [e for e in events if e.type == "model_call_end"]
        assert len(model_ends) <= 3


class TestHarnessPlugins:
    @pytest.mark.anyio
    async def test_plugin_runs_at_checkpoint(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        class TracePlugin(Plugin):
            def __init__(self):
                super().__init__("trace", [
                    {"hook_name": "after_model", "event_name": "trace_model", "mode": "side"},
                ])
                self.traced: list[str] = []

            async def handle(self, event_name: str, ctx: PluginContext) -> None:
                self.traced.append(event_name)

        trace = TracePlugin()
        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[trace], tool_manager=None)
        _events = [e async for e in harness.run("hi")]
        # Give side plugin task time to complete
        await asyncio.sleep(0.05)

        assert "trace_model" in trace.traced

    @pytest.mark.anyio
    async def test_blocking_plugin_runs_before_model(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        class PreModelPlugin(Plugin):
            def __init__(self):
                super().__init__("pre_model", [
                    {"hook_name": "before_model", "event_name": "inject_context", "mode": "blocking"},
                ])
                self.called = False

            async def handle(self, event_name: str, ctx: PluginContext) -> None:
                self.called = True
                ctx.agent.input("system", "injected context")

        plugin = PreModelPlugin()
        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[plugin], tool_manager=None)
        _events = [e async for e in harness.run("hi")]

        assert plugin.called
        # Injected system message should be before the user message (position=begin? no, by default it appends)
        # Actually, injected context by default appends to end, but the user input was already appended.
        # The system message should appear somewhere.
        msg_roles = [m.role for m in agent.state.messages]
        assert "system" in msg_roles


class TestHarnessPark:
    @pytest.mark.anyio
    async def test_agent_wait_triggers_park(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="ok, but wait", tool_calls=[], usage={}, finish_reason="stop")

        class WaitingPlugin(Plugin):
            def __init__(self):
                super().__init__("waiter", [
                    {"hook_name": "before_model", "event_name": "check_wait", "mode": "blocking"},
                ])

            async def handle(self, event_name: str, ctx: PluginContext) -> None:
                ctx.agent.wait("before_model", "test wait")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[WaitingPlugin()], tool_manager=None)

        events = []
        async def collect():
            async for e in harness.run("hi"):
                events.append(e)

        task = asyncio.ensure_future(collect())
        await asyncio.sleep(0.1)

        assert harness._parked
        assert any(e.type == "parked" for e in events)
        parked_event = next(e for e in events if e.type == "parked")
        assert "before_model" in str(parked_event.data)

        # Resolve
        wait_id = list(agent.state.waiting["before_model"])[0].wait_id
        resolved = await harness.resolve_wait(wait_id)
        assert resolved

        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.anyio
    async def test_resolve_wait_with_injected_message(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="done", tool_calls=[], usage={}, finish_reason="stop")

        class Waiter(Plugin):
            def __init__(self):
                super().__init__("waiter", [
                    {"hook_name": "before_tools", "event_name": "wait_approval", "mode": "blocking"},
                ])

            async def handle(self, event_name: str, ctx: PluginContext) -> None:
                ctx.agent.wait("before_tools", "needs approval")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[Waiter()], tool_manager=FakeToolExecutor())
        # Override model to return tool calls first
        original_call = agent._call_model
        called = False

        async def tool_then_text(messages, tools=None):
            nonlocal called
            if not called:
                called = True
                return ModelResult(
                    content="",
                    tool_calls=[{"id": "t1", "name": "echo", "params": {}}],
                    usage={}, finish_reason="tool_calls",
                )
            return ModelResult(content="all done", tool_calls=[], usage={}, finish_reason="stop")

        agent._call_model = tool_then_text

        events = []
        async def collect():
            async for e in harness.run("do it"):
                events.append(e)

        task = asyncio.ensure_future(collect())
        await asyncio.sleep(0.1)

        assert harness._parked

        # Resolve with injected message
        wait_id = list(agent.state.waiting["before_tools"])[0].wait_id
        resolved = await harness.resolve_wait(wait_id, {"role": "user", "content": "approved, go ahead"})
        assert resolved

        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestHarnessSessionId:
    @pytest.mark.anyio
    async def test_session_id_auto_assigned(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        assert agent.state.session_id == ""
        harness = AgentHarness(agent, plugins=[])
        _events = [e async for e in harness.run("hi")]
        assert agent.state.session_id != ""

    @pytest.mark.anyio
    async def test_session_id_explicit(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[])
        _events = [e async for e in harness.run("hi", session_id="my-session")]
        assert agent.state.session_id == "my-session"

    @pytest.mark.anyio
    async def test_session_id_not_overwritten_on_second_run(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="ok", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        harness = AgentHarness(agent, plugins=[])
        _events = [e async for e in harness.run("first", session_id="s1")]
        first_sid = agent.state.session_id
        _events = [e async for e in harness.run("second", session_id="s2")]
        # session_id changes when a different one is requested
        assert agent.state.session_id == "s2"


class TestHarnessEventBus:
    @pytest.mark.anyio
    async def test_events_flow_to_event_bus(self):
        async def fake_call(messages, tools=None):
            return ModelResult(content="hi", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)
        bus = InMemoryEventBus()
        harness = AgentHarness(agent, plugins=[], event_bus=bus)
        _events = [e async for e in harness.run("hello")]

        assert bus.event_count() >= 1
        model_events = bus.collected("model_call_end")
        assert len(model_events) == 1
        assert model_events[0].session_id == agent.state.session_id


class TestToolFilter:
    """Tests for _filter_tools — especially ToolConfig vs string comparison."""

    @pytest.mark.anyio
    async def test_filter_tools_with_toolconfig_list(self):
        """_filter_tools must extract names from ToolConfig objects, not compare strings to objects."""
        async def fake_call(messages, tools=None):
            return ModelResult(content="Hello", tool_calls=[], usage={}, finish_reason="stop")

        agent = make_agent(fake_call)

        # tools: [ToolConfig(name="read_file")] — only read_file from user should pass
        agent_config = AgentConfig(
            name="test-agent",
            tools=[ToolConfig(name="read_file")],
        )

        class FilterAwareExecutor:
            async def get_tool_definitions(self):
                return [
                    {"name": "kernel__builtin_echo", "params": {}},    # kernel — always included
                    {"name": "user__read_file", "params": {}},          # user + in tools list → included
                    {"name": "user__write_file", "params": {}},         # user + NOT in tools list → excluded
                    {"name": "filesystem__read", "params": {}},         # plugin — not in plugin_names → excluded
                ]

            async def execute(self, name, params):
                from tests.test_harness_engine import FakeToolResult
                return FakeToolResult()

        executor = FilterAwareExecutor()
        harness = AgentHarness(agent, plugins=[], tool_manager=executor, agent_config=agent_config)
        events = [e async for e in harness.run("test")]

        # Check model_call_end events contain tool_definitions
        model_ends = [e for e in events if e.type == "model_call_end"]
        assert len(model_ends) >= 1

        # The first model_call_end should have the filtered tool definitions
        tool_defs = model_ends[0].data.get("tool_definitions")
        assert tool_defs is not None, "model_call_end must include tool_definitions"
        names = {t["name"] for t in tool_defs}
        assert "kernel__builtin_echo" in names, "kernel tools must be included"
        assert "user__read_file" in names, "user__read_file must be included (in tools list)"
        assert "user__write_file" not in names, "user__write_file must be excluded (not in tools list)"
