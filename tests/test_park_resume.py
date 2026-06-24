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
        result = asyncio.run(harness.resolve_wait(w1.wait_id))

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

        asyncio.run(harness.resolve_wait(w1.wait_id, inject_message={
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

        asyncio.run(harness.resolve_wait(w1.wait_id))  # no inject_message

        assert harness._messages_injected is False
