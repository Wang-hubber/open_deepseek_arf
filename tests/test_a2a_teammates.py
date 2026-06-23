"""Tests for A2A Teammates Plugin — peer messaging, park/resume, result persistence."""
import asyncio
from unittest.mock import MagicMock

import pytest

from arf.communication.agent_bus import InMemoryAgentBus
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.core.protocols.communication import AgentInfo, AgentMessage
from arf.harness.context import PluginContext
from arf.plugins.a2a_teammates.state import PeerTeamState
import arf.plugins.a2a_teammates.state as _state_mod


def _make_primitive_agent():
    async def call_model(messages, tools=None):
        return ModelResult(content="test response")

    async def stream_model(messages, tools=None):
        yield {"type": "chunk", "content": "test"}

    return PrimitiveAgent(
        agent_id="test_agent",
        model_config={"model_name": "test"},
        call_model=call_model,
        stream_model=stream_model,
    )


def _make_ctx(session_id="default_group__pm", agent=None):
    if agent is None:
        agent = _make_primitive_agent()
    agent.state.session_id = session_id
    ctx = PluginContext(
        agent=agent,
        session_id=session_id,
        event_bus=MagicMock(),
    )
    ctx.hook_data["_harness_ref"] = {
        "harness": MagicMock(),
        "tool_manager": MagicMock(),
        "plugins": [],
        "agent_config": None,
        "max_turns": 50,
    }
    return ctx


def _setup_fresh_state():
    """Create a fresh PeerTeamState and set it as the module-level slot."""
    _state_mod._state = PeerTeamState()
    _state_mod._state.agent_bus = InMemoryAgentBus()
    return _state_mod._state


def _teardown_state():
    _state_mod._state = None


class TestSendPeerMessage:
    @pytest.fixture(autouse=True)
    def setup_state(self):
        self.state = _setup_fresh_state()
        yield
        _teardown_state()

    @pytest.mark.anyio
    async def test_send_peer_message_dispatches_to_bus(self):
        from arf.plugins.a2a_teammates.tools.send_peer_message.function import execute

        # Register dev on bus with session_id
        await _state_mod._state.agent_bus.register(AgentInfo(name="default_group__dev", description="", capabilities=[]))

        result = await execute(
            to="default_group__dev",
            message="review login.py",
            type="task",
            session_id="default_group__pm",
        )
        assert result["ok"] is True
        assert "correlation_id" in result

        # Message should be in dev's inbox as JRPC request
        msgs = [m async for m in _state_mod._state.agent_bus.receive("default_group__dev")]
        assert len(msgs) == 1
        assert msgs[0].type == "request"
        assert msgs[0].sender == "default_group__pm"
        assert msgs[0].payload["jsonrpc"] == "2.0"
        assert msgs[0].payload["method"] == "task.assign"
        assert msgs[0].payload["params"]["message"] == "review login.py"

    @pytest.mark.anyio
    async def test_send_registers_pending_reply(self):
        from arf.plugins.a2a_teammates.tools.send_peer_message.function import execute

        await _state_mod._state.agent_bus.register(AgentInfo(name="default_group__dev", description="", capabilities=[]))

        result = await execute(
            to="default_group__dev",
            message="task",
            session_id="default_group__pm",
        )
        corr_id = result["correlation_id"]
        assert corr_id in _state_mod._state.pending_replies
        assert _state_mod._state.pending_replies[corr_id]["sender"] == "default_group__pm"
        assert _state_mod._state.pending_replies[corr_id]["receiver"] == "default_group__dev"


class TestPluginHooks:
    @pytest.fixture(autouse=True)
    def setup_state(self):
        self.state = _setup_fresh_state()
        self.state.data_dir = "./data"
        yield
        _teardown_state()

    def _make_plugin(self):
        from arf.plugins.a2a_teammates import PeerTeamPlugin
        return PeerTeamPlugin(
            name="a2a_teammates",
            config={
                "group_id": "default_group",
                "members": [
                    {"role": "pm", "agent_name": "pm_agent"},
                    {"role": "dev", "agent_name": "dev_agent"},
                ],
            },
        )

    @pytest.mark.anyio
    async def test_init_injects_team_context(self):
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__pm", agent=agent)

        await plugin.handle("init", ctx)

        # Two system messages: dynamic roster + static protocol
        system_msgs = [m for m in agent.state.messages if m.role == "system"]
        assert len(system_msgs) >= 2
        # Roster: session-specific
        assert "default_group" in str(system_msgs[0].content)
        assert "pm" in str(system_msgs[0].content)
        # Protocol: cache-friendly constant
        assert "Team Communication" in str(system_msgs[1].content)
        assert "send_peer_message" in str(system_msgs[1].content)
        assert "JRPC" in str(system_msgs[1].content)
        assert "do not do the" in str(system_msgs[1].content)
        assert "receiver's work yourself" in str(system_msgs[1].content)

    @pytest.mark.anyio
    async def test_init_captures_harness(self):
        plugin = self._make_plugin()
        ctx = _make_ctx(session_id="default_group__pm")

        await plugin.handle("init", ctx)

        assert "default_group__pm" in _state_mod._state.peer_harnesses

    @pytest.mark.anyio
    async def test_inject_and_park_drains_bus(self):
        plugin = self._make_plugin()

        # Put a JRPC task request in dev's inbox
        await _state_mod._state.agent_bus.register(
            AgentInfo(name="default_group__dev", description="", capabilities=[]))
        await _state_mod._state.agent_bus.send(AgentMessage(
            sender="default_group__pm", receiver="default_group__dev", type="request",
            payload={"jsonrpc": "2.0", "method": "task.assign",
                     "params": {"message": "review login.py"}, "id": "peer_123"},
            priority="normal",
            correlation_id="peer_123",
        ))

        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await plugin.handle("inject_and_park", ctx)

        # Should have injected the peer message as user role with name attribution
        user_msgs = [m for m in agent.state.messages if m.role == "user"]
        peer_msgs = [m for m in user_msgs if m.name and m.name.startswith("peer:")]
        assert len(peer_msgs) == 1
        assert peer_msgs[0].name == "peer:default_group__pm"
        assert "[task]" in str(peer_msgs[0].content)
        assert "review login.py" in str(peer_msgs[0].content)




    @pytest.mark.anyio
    async def test_inject_and_park_parks_when_idle(self):
        """Idle worker (not entry_point) parks at before_round."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        _state_mod._state.peer_harnesses["default_group__dev"] = \
            ctx.hook_data["_harness_ref"]["harness"]

        await plugin.handle("inject_and_park", ctx)

        assert len(agent.state.waiting.get("before_round", [])) > 0

    @pytest.mark.anyio
    async def test_inject_and_park_skips_when_entry_point(self):
        """Entry point with no pending skips park."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__pm", agent=agent)

        _state_mod._state.entry_points["default_group__pm"] = True

        await plugin.handle("inject_and_park", ctx)

        assert "before_round" not in agent.state.waiting

    @pytest.mark.anyio
    async def test_heartbeat_updates_last_activity(self):
        plugin = self._make_plugin()
        ctx = _make_ctx(session_id="default_group__pm")

        await plugin.handle("heartbeat", ctx)

        assert "default_group__pm" in _state_mod._state.last_activity
        assert _state_mod._state.last_activity["default_group__pm"] > 0

    @pytest.mark.anyio
    async def test_park_receives_cancel_event(self):
        """_park_for_peer passes cancel_event from hook_data to wait loop."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__pm", agent=agent)

        _state_mod._state.peer_harnesses["default_group__pm"] = \
            ctx.hook_data["_harness_ref"]["harness"]
        _state_mod._state.entry_points["default_group__pm"] = True

        # Set a cancel_event in hook_data
        cancel_evt = asyncio.Event()
        ctx.hook_data["_cancel_event"] = cancel_evt

        # Make send_peer_message trigger park
        ctx.hook_data["_pending_tool_calls"] = [{
            "name": "send_peer_message",
            "params": {"to": "default_group__dev", "message": "hi"},
        }]

        # inject_session_id sets _peer_just_sent=True
        ctx.hook_data["_peer_just_sent"] = True

        await plugin.handle("park_after_send", ctx)

        # The spawned task should exist and have been passed the cancel_event
        task = _state_mod._state._wait_tasks.get("default_group__pm")
        assert task is not None, "Wait task should have been spawned"

        # Cleanup: cancel the task
        cancel_evt.set()
        await asyncio.sleep(0.1)
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class TestCancelPeerTask:
    @pytest.fixture(autouse=True)
    def setup_state(self):
        self.state = _setup_fresh_state()
        yield
        _teardown_state()

    @pytest.mark.anyio
    async def test_cancel_removes_pending_and_sends_to_bus(self):
        from arf.plugins.a2a_teammates.tools.cancel_peer_task.function import execute

        await _state_mod._state.agent_bus.register(
            AgentInfo(name="default_group__dev", description="", capabilities=[]))

        _state_mod._state.pending_replies["peer_123"] = {
            "sender": "default_group__pm",
            "receiver": "default_group__dev",
        }

        result = await execute(
            correlation_id="peer_123",
            session_id="default_group__pm",
        )
        assert result["ok"] is True
        assert result["cancelled"] is True
        assert "peer_123" not in _state_mod._state.pending_replies

        msgs = [m async for m in _state_mod._state.agent_bus.receive("default_group__dev")]
        assert len(msgs) == 1
        assert msgs[0].type == "notification"
        assert msgs[0].payload["method"] == "task.cancel"
        assert msgs[0].payload["params"]["correlation_id"] == "peer_123"

    @pytest.mark.anyio
    async def test_cancel_sends_to_bus_and_pops_pending(self):
        from arf.plugins.a2a_teammates.tools.cancel_peer_task.function import execute

        await _state_mod._state.agent_bus.register(
            AgentInfo(name="default_group__dev", description="", capabilities=[]))

        _state_mod._state.pending_replies["peer_456"] = {
            "sender": "default_group__pm",
            "receiver": "default_group__dev",
        }

        result = await execute(
            correlation_id="peer_456",
            session_id="default_group__pm",
        )
        assert result["ok"] is True
        assert "peer_456" not in _state_mod._state.pending_replies

        # Cancel notification sent to bus
        msgs = [m async for m in _state_mod._state.agent_bus.receive("default_group__dev")]
        assert len(msgs) == 1
        assert msgs[0].type == "notification"
        assert msgs[0].payload["method"] == "task.cancel"
        assert msgs[0].payload["params"]["correlation_id"] == "peer_456"

    @pytest.mark.anyio
    async def test_cancel_unknown_task_fails(self):
        from arf.plugins.a2a_teammates.tools.cancel_peer_task.function import execute

        result = await execute(
            correlation_id="nonexistent",
            session_id="default_group__pm",
        )
        assert result["ok"] is False
        assert "no pending task" in result["error"]


class TestPeerWaitLoop:
    @pytest.fixture(autouse=True)
    def setup_state(self):
        self.state = _setup_fresh_state()
        yield
        _teardown_state()

    @pytest.mark.anyio
    async def test_loop_wakes_on_message(self):
        """_peer_wait_loop resolves wait when message arrives on bus."""
        from arf.plugins.a2a_teammates import _peer_wait_loop

        harness = MagicMock()
        harness.resolve_wait = MagicMock(
            return_value=asyncio.Future())
        harness.resolve_wait.return_value.set_result(True)

        await _state_mod._state.agent_bus.register(
            AgentInfo(name="default_group__pm", description="", capabilities=[]))
        await _state_mod._state.agent_bus.send(AgentMessage(
            sender="default_group__dev", receiver="default_group__pm", type="response",
            payload={"jsonrpc": "2.0",
                     "result": {"summary": "done", "result_file": "x.md"},
                     "id": "peer_rpl_001"},
            priority="normal",
            correlation_id="peer_rpl_001",
        ))

        await asyncio.wait_for(
            _peer_wait_loop(
                harness=harness,
                wait_id="wait_001",
                inbox_key="default_group__pm",
                bus=_state_mod._state.agent_bus,
                cancel_evt=None,
            ),
            timeout=5.0,
        )

        harness.resolve_wait.assert_called_once()
        call_args = harness.resolve_wait.call_args
        assert call_args[0][0] == "wait_001"
        assert call_args[1]["inject_message"]["name"] == "peer:default_group__dev"
        assert "done" in call_args[1]["inject_message"]["content"]

        # Inbox drained by _peer_wait_loop
        remaining = [m async for m in _state_mod._state.agent_bus.receive("default_group__pm")]
        assert len(remaining) == 0

    @pytest.mark.anyio
    async def test_second_park_cancels_first_wait_loop(self):
        """When an agent parks twice, the first wait loop is cancelled."""
        from arf.plugins.a2a_teammates import _peer_wait_loop

        harness = MagicMock()
        harness.resolve_wait = MagicMock(return_value=asyncio.Future())
        harness.resolve_wait.return_value.set_result(True)

        await _state_mod._state.agent_bus.register(
            AgentInfo(name="default_group__pm", description="", capabilities=[]))

        # First park spawns task
        _state_mod._state._wait_tasks["default_group__pm"] = \
            asyncio.create_task(_peer_wait_loop(
                harness=harness,
                wait_id="wait_1",
                inbox_key="default_group__pm",
                bus=_state_mod._state.agent_bus,
                cancel_evt=None,
                idle_timeout=600.0,
                last_activity=_state_mod._state.last_activity,
            ))

        task1 = _state_mod._state._wait_tasks["default_group__pm"]
        assert not task1.done()

        # Simulate a second park — this should cancel task1
        if "default_group__pm" in _state_mod._state._wait_tasks:
            old = _state_mod._state._wait_tasks.pop("default_group__pm")
            if not old.done():
                old.cancel()
                # Yield to event loop so cancellation propagates
                await asyncio.sleep(0)

        # New task
        _state_mod._state._wait_tasks["default_group__pm"] = \
            asyncio.create_task(_peer_wait_loop(
                harness=harness,
                wait_id="wait_2",
                inbox_key="default_group__pm",
                bus=_state_mod._state.agent_bus,
                cancel_evt=None,
                idle_timeout=600.0,
                last_activity=_state_mod._state.last_activity,
            ))

        # task1 should be done (cancelled)
        assert task1.done()
        assert task1.cancelled()

        # Clean up
        task2 = _state_mod._state._wait_tasks.pop("default_group__pm")
        if not task2.done():
            task2.cancel()
            try:
                await task2
            except asyncio.CancelledError:
                pass


class TestForwardReply:
    @pytest.fixture(autouse=True)
    def setup_state(self):
        self.state = _setup_fresh_state()
        self.state.data_dir = "./data"
        yield
        _teardown_state()

    def _make_plugin(self):
        from arf.plugins.a2a_teammates import PeerTeamPlugin
        return PeerTeamPlugin(
            name="a2a_teammates",
            config={
                "group_id": "default_group",
                "members": [
                    {"role": "pm", "agent_name": "pm_agent"},
                    {"role": "dev", "agent_name": "dev_agent"},
                ],
            },
        )

    @pytest.mark.anyio
    async def test_forward_reply_extracts_task_complete_result(self):
        """forward_reply extracts task_complete result, sends bus reply, clears pending."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await _state_mod._state.agent_bus.register(
            AgentInfo(name="pm", description="", capabilities=[]))

        # Register a pending reply: pm sent a task to dev
        _state_mod._state.pending_replies["peer_abc"] = {
            "sender": "default_group__pm", "receiver": "default_group__dev",
        }

        # Inject assistant message + task_complete tool result
        ctx.agent.input(role="assistant", content="I have completed the review.")
        ctx.agent.input(role="tool", content={
            "tool_call_id": "call_1",
            "name": "task_complete",
            "result": {
                "ok": True,
                "task_complete": True,
                "result": "Found 3 security issues: XSS in login.py, SQL injection in db.py",
                "files_changed": {},
                "confidence": 1.0,
                "notes": "",
            },
            "error": "",
        })

        await plugin.handle("forward_reply", ctx)

        # Verify JRPC response sent to pm
        msgs = [m async for m in _state_mod._state.agent_bus.receive("default_group__pm")]
        assert len(msgs) == 1
        reply = msgs[0]
        assert reply.sender == "default_group__dev"
        assert reply.receiver == "default_group__pm"
        assert reply.type == "response"
        assert reply.payload["jsonrpc"] == "2.0"
        assert reply.payload["id"] == "peer_abc"
        assert "Found 3 security issues" in reply.payload["result"]["summary"]

        # Verify pending cleared
        assert "peer_abc" not in _state_mod._state.pending_replies

    @pytest.mark.anyio
    async def test_forward_reply_skips_without_task_complete(self):
        """forward_reply skips when no task_complete — pending stays for next round."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await _state_mod._state.agent_bus.register(
            AgentInfo(name="pm", description="", capabilities=[]))

        _state_mod._state.pending_replies["peer_def"] = {
            "sender": "default_group__pm", "receiver": "default_group__dev",
        }

        # Only an assistant message, no task_complete tool result
        ctx.agent.input(role="assistant", content="I did some work.")

        await plugin.handle("forward_reply", ctx)

        # No reply sent — task_complete is required to forward
        msgs = [m async for m in _state_mod._state.agent_bus.receive("default_group__pm")]
        assert len(msgs) == 0

        # Pending NOT cleared — agent hasn't finished yet
        assert "peer_def" in _state_mod._state.pending_replies

    @pytest.mark.anyio
    async def test_forward_reply_skips_when_no_pending(self):
        """forward_reply is no-op when no pending replies target this role."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await plugin.handle("forward_reply", ctx)

        # No pending matches dev — no message should appear on bus
        msgs = [m async for m in _state_mod._state.agent_bus.receive("default_group__dev")]
        assert len(msgs) == 0
        assert len(_state_mod._state.pending_replies) == 0

    @pytest.mark.anyio
    async def test_forward_reply_clears_pending_on_empty_task_complete(self):
        """forward_reply clears pending even when task_complete result is empty string."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await _state_mod._state.agent_bus.register(
            AgentInfo(name="pm", description="", capabilities=[]))

        _state_mod._state.pending_replies["peer_empty"] = {
            "sender": "default_group__pm", "receiver": "default_group__dev",
        }

        # Assistant message + task_complete with empty result string
        ctx.agent.input(role="assistant", content="Done, nothing to report.")
        ctx.agent.input(role="tool", content={
            "tool_call_id": "call_empty",
            "name": "task_complete",
            "result": {
                "ok": True,
                "task_complete": True,
                "result": "",
                "files_changed": {},
                "confidence": 1.0,
                "notes": "",
            },
            "error": "",
        })

        await plugin.handle("forward_reply", ctx)

        # Should still forward (empty summary) and clear pending
        msgs = [m async for m in _state_mod._state.agent_bus.receive("default_group__pm")]
        assert len(msgs) == 1
        assert msgs[0].payload["result"]["summary"] == "(no output)"

        # Pending MUST be cleared — this is the fix
        assert "peer_empty" not in _state_mod._state.pending_replies
