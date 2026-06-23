"""Tests for A2A Teammates Plugin — peer messaging, park/resume, result persistence."""
import asyncio
from unittest.mock import MagicMock

import pytest

from arf.communication.agent_bus import InMemoryAgentBus
from arf.agent.primitive import PrimitiveAgent
from arf.agent.state import ModelResult
from arf.core.protocols.communication import AgentInfo, AgentMessage
from arf.harness.context import PluginContext
from arf.plugins.a2a_teammates.tools import _registry as tm_registry


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


class TestSendPeerMessage:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        tm_registry.agent_bus = InMemoryAgentBus()
        tm_registry._pending_replies.clear()
        tm_registry._peer_context_injected.clear()
        tm_registry._peer_harnesses.clear()
        yield
        tm_registry.agent_bus = None

    @pytest.mark.anyio
    async def test_send_peer_message_dispatches_to_bus(self):
        from arf.plugins.a2a_teammates.tools.send_peer_message.function import execute

        # Register dev on bus with session_id
        await tm_registry.agent_bus.register(AgentInfo(name="default_group__dev", description="", capabilities=[]))

        result = await execute(
            to="default_group__dev",
            message="review login.py",
            type="task",
            session_id="default_group__pm",
        )
        assert result["ok"] is True
        assert "correlation_id" in result

        # Message should be in dev's inbox (keyed by session_id)
        msgs = [m async for m in tm_registry.agent_bus.receive("default_group__dev")]
        assert len(msgs) == 1
        assert msgs[0].payload["message"] == "review login.py"
        assert msgs[0].sender == "default_group__pm"

    @pytest.mark.anyio
    async def test_send_registers_pending_reply(self):
        from arf.plugins.a2a_teammates.tools.send_peer_message.function import execute

        await tm_registry.agent_bus.register(AgentInfo(name="default_group__dev", description="", capabilities=[]))

        result = await execute(
            to="default_group__dev",
            message="task",
            session_id="default_group__pm",
        )
        corr_id = result["correlation_id"]
        assert corr_id in tm_registry._pending_replies
        assert tm_registry._pending_replies[corr_id]["sender"] == "default_group__pm"
        assert tm_registry._pending_replies[corr_id]["receiver"] == "default_group__dev"


class TestPluginHooks:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        tm_registry.agent_bus = InMemoryAgentBus()
        tm_registry._pending_replies.clear()
        tm_registry._peer_context_injected.clear()
        tm_registry._peer_harnesses.clear()
        tm_registry._peer_wait_ids.clear()
        tm_registry.data_dir = "./data"
        yield
        tm_registry.agent_bus = None

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
        assert "do not do the receiver's work" in str(system_msgs[1].content)

    @pytest.mark.anyio
    async def test_init_captures_harness(self):
        plugin = self._make_plugin()
        ctx = _make_ctx(session_id="default_group__pm")

        await plugin.handle("init", ctx)

        assert "default_group__pm" in tm_registry._peer_harnesses

    @pytest.mark.anyio
    async def test_inject_peer_msgs_drains_bus(self):
        plugin = self._make_plugin()

        # Put a message in dev's inbox
        await tm_registry.agent_bus.register(
            AgentInfo(name="default_group__dev", description="", capabilities=[]))
        await tm_registry.agent_bus.send(AgentMessage(
            sender="default_group__pm", receiver="default_group__dev", type="task",
            payload={"message": "review login.py"},
            priority="normal",
            correlation_id="peer_123",
        ))

        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await plugin.handle("inject_peer_msgs", ctx)

        # Should have injected the peer message
        system_msgs = [m for m in agent.state.messages if m.role == "system"]
        peer_msgs = [m for m in system_msgs if "[Peer task from default_group__pm]" in str(m.content)]
        assert len(peer_msgs) == 1
        assert "review login.py" in str(peer_msgs[0].content)




    @pytest.mark.anyio
    async def test_inject_peer_msgs_parks_when_idle(self):
        """Idle worker (not entry_point) parks at before_model."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        tm_registry._peer_harnesses["default_group__dev"] = \
            ctx.hook_data["_harness_ref"]["harness"]

        await plugin.handle("inject_peer_msgs", ctx)

        assert len(agent.state.waiting.get("before_model", [])) > 0
        assert "default_group__dev" in tm_registry._peer_wait_ids

    @pytest.mark.anyio
    async def test_inject_peer_msgs_skips_when_entry_point(self):
        """Entry point with no pending skips park."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__pm", agent=agent)

        tm_registry._entry_points["default_group__pm"] = True

        await plugin.handle("inject_peer_msgs", ctx)

        assert "before_model" not in agent.state.waiting

    @pytest.mark.anyio
    async def test_heartbeat_updates_last_activity(self):
        plugin = self._make_plugin()
        ctx = _make_ctx(session_id="default_group__pm")

        await plugin.handle("heartbeat", ctx)

        assert "default_group__pm" in tm_registry._last_activity
        assert tm_registry._last_activity["default_group__pm"] > 0


class TestCancelPeerTask:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        tm_registry.agent_bus = InMemoryAgentBus()
        tm_registry._pending_replies.clear()
        tm_registry._peer_harnesses.clear()
        tm_registry._peer_wait_ids.clear()
        yield
        tm_registry.agent_bus = None

    @pytest.mark.anyio
    async def test_cancel_removes_pending_and_sends_to_bus(self):
        from arf.plugins.a2a_teammates.tools.cancel_peer_task.function import execute

        await tm_registry.agent_bus.register(
            AgentInfo(name="default_group__dev", description="", capabilities=[]))

        tm_registry._pending_replies["peer_123"] = {
            "sender": "default_group__pm",
            "receiver": "default_group__dev",
        }

        result = await execute(
            correlation_id="peer_123",
            session_id="default_group__pm",
        )
        assert result["ok"] is True
        assert result["cancelled"] is True
        assert "peer_123" not in tm_registry._pending_replies

        msgs = [m async for m in tm_registry.agent_bus.receive("default_group__dev")]
        assert len(msgs) == 1
        assert msgs[0].type == "cancel"

    @pytest.mark.anyio
    async def test_cancel_wakes_parked_receiver(self):
        from arf.plugins.a2a_teammates.tools.cancel_peer_task.function import execute
        from unittest.mock import MagicMock

        tm_registry._pending_replies["peer_456"] = {
            "sender": "default_group__pm",
            "receiver": "default_group__dev",
        }

        harness_mock = MagicMock()
        harness_mock.resolve_wait = MagicMock()
        fut = asyncio.Future()
        fut.set_result(True)
        harness_mock.resolve_wait.return_value = fut

        tm_registry._peer_harnesses["default_group__dev"] = harness_mock
        tm_registry._peer_wait_ids["default_group__dev"] = "wait_dev_001"

        result = await execute(
            correlation_id="peer_456",
            session_id="default_group__pm",
        )
        assert result["ok"] is True

        harness_mock.resolve_wait.assert_called_once_with("wait_dev_001")

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
    def setup_registry(self):
        tm_registry.agent_bus = InMemoryAgentBus()
        tm_registry._pending_replies.clear()
        yield
        tm_registry.agent_bus = None

    @pytest.mark.anyio
    async def test_loop_wakes_on_message(self):
        """_peer_wait_loop resolves wait when message arrives on bus."""
        from arf.plugins.a2a_teammates import _peer_wait_loop

        harness = MagicMock()
        harness.resolve_wait = MagicMock(
            return_value=asyncio.Future())
        harness.resolve_wait.return_value.set_result(True)

        await tm_registry.agent_bus.register(
            AgentInfo(name="default_group__pm", description="", capabilities=[]))
        await tm_registry.agent_bus.send(AgentMessage(
            sender="default_group__dev", receiver="default_group__pm", type="reply",
            payload={"message": "done", "result_file": "x.md"},
            priority="normal",
            correlation_id="peer_rpl_001",
        ))

        await asyncio.wait_for(
            _peer_wait_loop(
                harness=harness,
                wait_id="wait_001",
                inbox_key="default_group__pm",
                bus=tm_registry.agent_bus,
                cancel_evt=None,
                data_dir="./data",
                group_id="default_group",
            ),
            timeout=5.0,
        )

        harness.resolve_wait.assert_called_once_with("wait_001")

        # Message stays in inbox — inject_peer_msgs drains it at before_model
        remaining = [m async for m in tm_registry.agent_bus.receive("default_group__pm")]
        assert len(remaining) == 1
        assert remaining[0].payload["message"] == "done"


class TestForwardReply:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        tm_registry.agent_bus = InMemoryAgentBus()
        tm_registry._pending_replies.clear()
        tm_registry._peer_context_injected.clear()
        tm_registry._peer_harnesses.clear()
        tm_registry.data_dir = "./data"
        yield
        tm_registry.agent_bus = None

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

        await tm_registry.agent_bus.register(
            AgentInfo(name="pm", description="", capabilities=[]))

        # Register a pending reply: pm sent a task to dev
        tm_registry._pending_replies["peer_abc"] = {
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

        # Verify reply sent to pm
        msgs = [m async for m in tm_registry.agent_bus.receive("default_group__pm")]
        assert len(msgs) == 1
        reply = msgs[0]
        assert reply.sender == "default_group__dev"
        assert reply.receiver == "default_group__pm"
        assert reply.type == "reply"
        assert "Found 3 security issues" in reply.payload["brief"]
        assert reply.payload["correlation_id"] == "peer_abc"

        # Verify pending cleared
        assert "peer_abc" not in tm_registry._pending_replies

    @pytest.mark.anyio
    async def test_forward_reply_skips_without_task_complete(self):
        """forward_reply skips when no task_complete — pending stays for next round."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await tm_registry.agent_bus.register(
            AgentInfo(name="pm", description="", capabilities=[]))

        tm_registry._pending_replies["peer_def"] = {
            "sender": "default_group__pm", "receiver": "default_group__dev",
        }

        # Only an assistant message, no task_complete tool result
        ctx.agent.input(role="assistant", content="I did some work.")

        await plugin.handle("forward_reply", ctx)

        # No reply sent — task_complete is required to forward
        msgs = [m async for m in tm_registry.agent_bus.receive("default_group__pm")]
        assert len(msgs) == 0

        # Pending NOT cleared — agent hasn't finished yet
        assert "peer_def" in tm_registry._pending_replies

    @pytest.mark.anyio
    async def test_forward_reply_skips_when_no_pending(self):
        """forward_reply is no-op when no pending replies target this role."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await plugin.handle("forward_reply", ctx)

        # No pending matches dev — no message should appear on bus
        msgs = [m async for m in tm_registry.agent_bus.receive("default_group__dev")]
        assert len(msgs) == 0
        assert len(tm_registry._pending_replies) == 0
