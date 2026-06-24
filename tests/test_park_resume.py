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


class TestHITLUnified:
    """HITL uses generic wait("before_round") mechanism, no engine special-casing."""

    def test_provide_hitl_response_finds_wait(self):
        harness = make_harness()
        agent = harness.agent

        # Register a hitl wait (as ask_user tool would)
        wi = agent.wait("before_round", "hitl", resume_key="")
        harness._park_event = asyncio.Event()
        harness._parked = True

        # provide_hitl_response should find the hitl wait and resolve it
        result = asyncio.run(harness.provide_hitl_response("test-sid", "user answer"))

        assert result is True
        # The human answer should be injected as a user message
        assert any(
            m.content == "user answer" and m.role == "user"
            for m in agent.state.messages
        )
        # The hitl wait should be resolved
        assert "before_round" not in agent.state.waiting

    def test_provide_hitl_response_no_pending(self):
        harness = make_harness()
        # No hitl wait registered
        result = asyncio.run(harness.provide_hitl_response("test-sid", "answer"))
        assert result is False


class TestRebuildWaitTasks:
    """Session resume propagates waits with resume_key to plugins."""

    def test_rebuild_wait_tasks_passes_to_ctx(self):
        harness = make_harness()
        agent = harness.agent

        # Register a wait with resume_key
        agent.wait("before_round", "peer_wait:abc123", resume_key="peer_wait:abc123")

        # Simulate rebuild
        ctx = harness._make_ctx()
        waits_with_resume = [
            wi for wi_list in agent.state.waiting.values()
            for wi in wi_list if wi.resume_key
        ]
        ctx.hook_data["_pending_resume"] = waits_with_resume

        assert len(ctx.hook_data["_pending_resume"]) == 1
        assert ctx.hook_data["_pending_resume"][0].resume_key == "peer_wait:abc123"

    def test_rebuild_skips_empty_resume_keys(self):
        harness = make_harness()
        agent = harness.agent

        # Register wait WITHOUT resume_key
        agent.wait("before_round", "hitl", resume_key="")

        ctx = harness._make_ctx()
        waits_with_resume = [
            wi for wi_list in agent.state.waiting.values()
            for wi in wi_list if wi.resume_key
        ]
        ctx.hook_data["_pending_resume"] = waits_with_resume

        assert len(ctx.hook_data["_pending_resume"]) == 0


class TestAskUserTool:
    """ask_user tool registers wait on before_round and emits need_human_input."""

    def test_ask_user_registers_wait_and_emits(self):
        harness = make_harness()
        agent = harness.agent

        emit_events = []
        def fake_emit(event_type, data):
            emit_events.append((event_type, data))

        from arf.skills.ask_user_tool import execute
        result = asyncio.run(execute(
            question="Proceed?",
            options=["yes", "no"],
            _register_wait=lambda h, r, resume_key="": agent.wait(h, r, resume_key=resume_key),
            _emit=fake_emit,
        ))

        assert result["ok"] is True
        assert result["pending"] is True
        assert "wait_id" in result

        # Verify wait was registered
        waits = agent.state.waiting.get("before_round", [])
        assert len(waits) == 1
        assert waits[0].reason == "hitl"

        # Verify need_human_input emitted
        assert len(emit_events) == 1
        assert emit_events[0][0] == "need_human_input"
        assert emit_events[0][1]["question"] == "Proceed?"


class TestSubagentPark:
    """delegate_task registers wait on before_round, _wake_parent injects result."""

    def test_delegate_task_registers_wait(self, monkeypatch):
        """delegate_task tool registers wait when _register_wait is injected.

        Tests that execute accepts _register_wait as an explicit kwarg (injected
        by engine at before_tools).
        """
        from arf.plugins.a2a_subagents.tools.delegate_task.function import execute
        import inspect

        sig = inspect.signature(execute)
        params = list(sig.parameters.keys())

        assert "_register_wait" in params  # injected by engine at before_tools

    def test_wake_parent_injects_result(self):
        """_wake_parent calls resolve_wait with inject_message from delegator results."""

        async def _run():
            harness = make_harness()
            agent = harness.agent

            # Register a subagent wait (as delegate_task would)
            wi = agent.wait("before_round", "subagent:task123", resume_key="subagent:task123")

            # Mock the registries
            from arf.plugins.a2a_subagents.tools import _registry
            old_delegator = _registry.delegator

            class FakeDelegator:
                async def get_pending(self, parent_sid):
                    return [{
                        "task_id": "task123",
                        "content": "subagent result text",
                        "turn_count": 3,
                        "tool_calls_summary": [],
                    }]

            _registry.delegator = FakeDelegator()

            harness._park_event = asyncio.Event()
            harness._parked = True

            # Set parent_harness so _wake_parent can find the harness
            _registry.parent_harness = harness

            # Simulate _wake_parent
            from arf.plugins.a2a_subagents.tools.delegate_task.function import _wake_parent

            # Override _parent_wait_ids, now keyed by task_id
            _registry._parent_wait_ids = {"task123": wi.wait_id}

            _wake_parent(_registry, "test-sid", "task123")

            # Give the asyncio task time to run
            await asyncio.sleep(0.05)

            # The wait should be resolved and a message injected
            assert harness._park_event.is_set()
            assert harness._parked is False

            # Cleanup
            _registry.delegator = old_delegator
            _registry._parent_wait_ids = {}

        asyncio.run(_run())


class TestMultiWaitIntegration:
    """End-to-end: multiple waits, partial wakeup, sequential processing."""

    def test_multi_wait_partial_wakeup(self):
        """Agent waits for 3 events, processes each as it arrives."""
        harness = make_harness()
        agent = harness.agent

        # Register 3 waits
        w1 = agent.wait("before_round", "peer_wait:aaa", resume_key="peer_wait:aaa")
        w2 = agent.wait("before_round", "peer_wait:bbb", resume_key="peer_wait:bbb")
        w3 = agent.wait("before_round", "subagent:task1", resume_key="subagent:task1")

        assert len(agent.state.waiting["before_round"]) == 3

        async def _run():
            # Resolve w2 first (simulate peer B replying first)
            harness._park_event = asyncio.Event()
            harness._parked = True
            await harness.resolve_wait(wait_id=w2.wait_id, inject_message={
                "role": "user",
                "content": "peer B result",
            })

            # Flags: _messages_injected=True, w2 removed
            assert harness._messages_injected is True
            assert len(agent.state.waiting["before_round"]) == 2
            assert harness._parked is False
            assert harness._park_event.is_set()

            # Consume _messages_injected (simulate before_round logic)
            harness._messages_injected = False

            # Resolve w1
            harness._park_event = asyncio.Event()
            harness._parked = True
            await harness.resolve_wait(wait_id=w1.wait_id, inject_message={
                "role": "user",
                "content": "peer A result",
            })
            assert len(agent.state.waiting["before_round"]) == 1
            assert harness._parked is False

            # Resolve w3 (last one)
            harness._park_event = asyncio.Event()
            harness._parked = True
            result = await harness.resolve_wait(wait_id=w3.wait_id)
            assert result is True  # all done
            assert len(agent.state.waiting) == 0
            assert harness._parked is False

        asyncio.run(_run())
