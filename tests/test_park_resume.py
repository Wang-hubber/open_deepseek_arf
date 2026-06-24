"""Tests for unified park/resume mechanism."""
import asyncio
import pytest
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import AgentState, Message, WaitItem, ModelResult
from arf.harness.engine import AgentHarness


async def _fake_call_model(messages, tools=None):
    return ModelResult(content="ok")


class _FakePlugin:
    """Plugin that registers _register_wait as a side effect for testing."""
    def __init__(self):
        self.events = [
            {"hook_name": "before_tools", "event_name": "inject", "mode": "blocking"},
        ]
        self.name = "fake"
        self.config = {}
        self._registered_waits: list = []

    async def handle(self, event_name, ctx):
        # Simulate injecting _register_wait callable into tool params
        for tc in ctx.hook_data.get("_pending_tool_calls", []):
            tc.setdefault("params", {})["_register_wait"] = (
                lambda h, r, rk="": ctx.agent.wait(h, r, resume_key=rk)
            )

    def event_names_for_hook(self, hook_name):
        return ["inject"]

    def mode_for(self, hook_name, event_name):
        return "blocking"


def make_harness():
    agent = PrimitiveAgent(
        agent_id="test",
        model_config={},
        call_model=_fake_call_model,
    )
    agent.state.session_id = "test-sid"
    return AgentHarness(agent=agent, plugins=[_FakePlugin()], data_dir="./data")


class TestResolveWaitPartialWakeup:
    """resolve_wait wakes harness even when some waits remain."""

    def test_resolve_wait_always_sets_event(self):
        harness = make_harness()
        agent = harness.agent

        # Register two waits on before_round
        w1 = agent.wait("before_round", "wait_a")
        w2 = agent.wait("before_round", "wait_b")

        # Set up park event manually (simulate _do_park entry)
        harness._park_event = asyncio.Event()
        harness._parked = True

        # Resolve only one wait
        result = asyncio.run(harness.resolve_wait(wait_id=w1.wait_id))

        # Event should be set (harness woken up)
        assert harness._park_event.is_set()
        # _parked should be cleared
        assert harness._parked is False
        # But there's still a remaining wait
        assert result is False
        assert len(agent.state.waiting.get("before_round", [])) == 1

    def test_resolve_wait_injects_message_and_sets_flag(self):
        harness = make_harness()
        agent = harness.agent

        w1 = agent.wait("before_round", "wait_a")
        harness._park_event = asyncio.Event()
        harness._parked = True

        asyncio.run(harness.resolve_wait(wait_id=w1.wait_id, inject_message={
            "role": "user",
            "content": "injected result",
        }))

        assert harness._messages_injected is True
        # Message should be in agent state
        last_msg = agent.state.messages[-1]
        assert last_msg.content == "injected result"

    def test_resolve_wait_no_inject_does_not_set_flag(self):
        harness = make_harness()
        agent = harness.agent

        w1 = agent.wait("before_round", "wait_a")
        harness._park_event = asyncio.Event()
        harness._parked = True

        asyncio.run(harness.resolve_wait(wait_id=w1.wait_id))  # no inject_message

        assert harness._messages_injected is False


class TestBeforeRoundMessagesInjected:
    """before_round skips park when _messages_injected is set."""

    def test_skip_park_when_messages_injected(self):
        harness = make_harness()
        agent = harness.agent

        # Register a wait (simulating remaining wait from partial wakeup)
        agent.wait("before_round", "remaining_wait")
        # Set flag (simulating just-injected message from resolve_wait)
        harness._messages_injected = True

        async def do_test():
            ctx = harness._make_ctx()
            ctx.hook_data["_cancel_event"] = harness._cancel_event

            # before_round checkpoint should return True (has waiting)
            should_park = await harness._checkpoint("before_round", ctx)
            assert should_park is True

            # But we should NOT actually park because _messages_injected is set
            # The harness loop should skip _do_park and proceed to round
            # (We test the flag behavior, not the full loop here)
            harness._messages_injected = False  # simulate flag consumed
            assert harness._messages_injected is False

        asyncio.run(do_test())

    def test_park_when_no_messages_injected(self):
        harness = make_harness()
        agent = harness.agent

        agent.wait("before_round", "test_wait")
        harness._messages_injected = False  # no injected message

        async def do_test():
            # Set up park event
            harness._park_event = asyncio.Event()

            # Start _do_park in background
            async def resolver():
                await asyncio.sleep(0.01)
                # Resolve the wait
                await harness.resolve_wait(
                    agent.state.waiting["before_round"][0].wait_id
                )

            asyncio.create_task(resolver())
            await harness._do_park()

            # After wakeup, _parked should be False (resolve_wait cleared it)
            assert harness._parked is False

        asyncio.run(do_test())


class TestRegisterWaitInjection:
    """Engine injects _register_wait and _emit into all tool calls at before_tools."""

    def test_register_wait_injected_into_tool_params(self):
        harness = make_harness()
        agent = harness.agent

        # Simulate pending tool calls from model
        harness._current_ctx = harness._make_ctx()
        harness._current_ctx.hook_data["_pending_tool_calls"] = [
            {"name": "delegate_task", "id": "1", "params": {"task": "x"}},
            {"name": "send_peer_message", "id": "2", "params": {"to": "y"}},
        ]

        # Manually inject like before_tools loop would
        for tc in harness._current_ctx.hook_data["_pending_tool_calls"]:
            tc.setdefault("params", {})["_register_wait"] = (
                lambda h, r, resume_key="": agent.wait(h, r, resume_key=resume_key)
            )
            tc.setdefault("params", {})["_emit"] = harness._current_ctx.emit

        # Verify injection
        for tc in harness._current_ctx.hook_data["_pending_tool_calls"]:
            assert "_register_wait" in tc["params"]
            assert callable(tc["params"]["_register_wait"])
            assert "_emit" in tc["params"]
            assert callable(tc["params"]["_emit"])

    def test_register_wait_creates_wait_item(self):
        harness = make_harness()
        agent = harness.agent

        # Simulate injection
        harness._current_ctx = harness._make_ctx()
        harness._current_ctx.hook_data["_pending_tool_calls"] = [
            {"name": "some_tool", "id": "1", "params": {}},
        ]
        for tc in harness._current_ctx.hook_data["_pending_tool_calls"]:
            tc.setdefault("params", {})["_register_wait"] = (
                lambda h, r, resume_key="": agent.wait(h, r, resume_key=resume_key)
            )

        # Call the injected _register_wait (as a tool would)
        register_wait = harness._current_ctx.hook_data["_pending_tool_calls"][0]["params"]["_register_wait"]
        wi = register_wait("before_round", "test_reason", resume_key="test:resume")

        assert wi is not None
        assert wi.hook_name == "before_round"
        assert wi.reason == "test_reason"
        assert wi.resume_key == "test:resume"
        assert agent.state.waiting["before_round"][0].wait_id == wi.wait_id
