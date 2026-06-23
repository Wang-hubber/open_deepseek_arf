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

        # Register dev on bus first
        await tm_registry.agent_bus.register(AgentInfo(name="dev", description="", capabilities=[]))

        result = await execute(
            receiver="dev",
            message="review login.py",
            type="task",
            session_id="default_group__pm",
        )
        assert result["ok"] is True
        assert "correlation_id" in result

        # Message should be in dev's inbox
        msgs = [m async for m in tm_registry.agent_bus.receive("dev")]
        assert len(msgs) == 1
        assert msgs[0].payload["message"] == "review login.py"
        assert msgs[0].sender == "pm"

    @pytest.mark.anyio
    async def test_send_registers_pending_reply(self):
        from arf.plugins.a2a_teammates.tools.send_peer_message.function import execute

        await tm_registry.agent_bus.register(AgentInfo(name="dev", description="", capabilities=[]))

        result = await execute(
            receiver="dev",
            message="task",
            session_id="default_group__pm",
        )
        corr_id = result["correlation_id"]
        assert corr_id in tm_registry._pending_replies
        assert tm_registry._pending_replies[corr_id]["sender"] == "pm"
        assert tm_registry._pending_replies[corr_id]["receiver"] == "dev"


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

        # Team context should be injected as system message
        system_msgs = [m for m in agent.state.messages if m.role == "system"]
        assert len(system_msgs) >= 1
        assert "Team Communication" in str(system_msgs[0].content)
        assert "send_peer_message" in str(system_msgs[0].content)

    @pytest.mark.anyio
    async def test_init_captures_harness(self):
        plugin = self._make_plugin()
        ctx = _make_ctx(session_id="default_group__pm")

        await plugin.handle("init", ctx)

        assert "pm" in tm_registry._peer_harnesses

    @pytest.mark.anyio
    async def test_inject_peer_msgs_drains_bus(self):
        plugin = self._make_plugin()

        # Put a message in dev's inbox
        await tm_registry.agent_bus.register(
            AgentInfo(name="dev", description="", capabilities=[]))
        await tm_registry.agent_bus.send(AgentMessage(
            sender="pm", receiver="dev", type="task",
            payload={"message": "review login.py"},
            priority="normal",
            correlation_id="peer_123",
        ))

        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await plugin.handle("inject_peer_msgs", ctx)

        # Should have injected the peer message
        system_msgs = [m for m in agent.state.messages if m.role == "system"]
        peer_msgs = [m for m in system_msgs if "[Peer task from pm]" in str(m.content)]
        assert len(peer_msgs) == 1
        assert "review login.py" in str(peer_msgs[0].content)

    @pytest.mark.anyio
    async def test_peer_park_waits_when_pending_replies(self):
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__pm", agent=agent)

        # Register a pending reply expectation (pm → dev)
        tm_registry._pending_replies["peer_123"] = {
            "sender": "pm", "receiver": "dev",
        }

        # Simulate _on_init storing harness ref (C1 fix: harness must exist
        # before park, otherwise park is skipped to avoid deadlock)
        harness_mock = ctx.hook_data["_harness_ref"]["harness"]
        tm_registry._peer_harnesses["pm"] = harness_mock

        await plugin.handle("peer_park", ctx)

        # Should have registered a wait
        assert len(agent.state.waiting.get("after_round", [])) > 0
        assert "pm" in tm_registry._peer_wait_ids

    @pytest.mark.anyio
    async def test_peer_park_skips_when_no_pending(self):
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__pm", agent=agent)

        await plugin.handle("peer_park", ctx)

        # No pending → no wait registered
        assert "after_round" not in agent.state.waiting


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
            AgentInfo(name="pm", description="", capabilities=[]))
        await tm_registry.agent_bus.send(AgentMessage(
            sender="dev", receiver="pm", type="reply",
            payload={"message": "done", "result_file": "x.md"},
            priority="normal",
            correlation_id="peer_rpl_001",
        ))

        await asyncio.wait_for(
            _peer_wait_loop(
                harness=harness,
                wait_id="wait_001",
                role_key="pm",
                bus=tm_registry.agent_bus,
                cancel_evt=None,
                data_dir="./data",
                group_id="default_group",
            ),
            timeout=5.0,
        )

        harness.resolve_wait.assert_called_once()
        call_args = harness.resolve_wait.call_args
        # wait_id is first positional arg, inject_message is keyword arg
        assert call_args[0][0] == "wait_001"
        assert "done" in call_args[1]["inject_message"]["content"]


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
            "sender": "pm", "receiver": "dev",
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
        msgs = [m async for m in tm_registry.agent_bus.receive("pm")]
        assert len(msgs) == 1
        reply = msgs[0]
        assert reply.sender == "dev"
        assert reply.receiver == "pm"
        assert reply.type == "reply"
        assert "Found 3 security issues" in reply.payload["brief"]
        assert reply.payload["correlation_id"] == "peer_abc"

        # Verify pending cleared
        assert "peer_abc" not in tm_registry._pending_replies

    @pytest.mark.anyio
    async def test_forward_reply_uses_assistant_fallback(self):
        """forward_reply falls back to assistant content when no task_complete tool."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await tm_registry.agent_bus.register(
            AgentInfo(name="pm", description="", capabilities=[]))

        tm_registry._pending_replies["peer_def"] = {
            "sender": "pm", "receiver": "dev",
        }

        # Only an assistant message, no task_complete tool result
        ctx.agent.input(role="assistant", content="I did some work.")

        await plugin.handle("forward_reply", ctx)

        msgs = [m async for m in tm_registry.agent_bus.receive("pm")]
        assert len(msgs) == 1
        assert "I did some work." in msgs[0].payload["brief"]

        # Pending should be cleared
        assert "peer_def" not in tm_registry._pending_replies

    @pytest.mark.anyio
    async def test_forward_reply_skips_when_no_pending(self):
        """forward_reply is no-op when no pending replies target this role."""
        plugin = self._make_plugin()
        agent = _make_primitive_agent()
        ctx = _make_ctx(session_id="default_group__dev", agent=agent)

        await plugin.handle("forward_reply", ctx)

        # No pending matches dev — no message should appear on bus
        msgs = [m async for m in tm_registry.agent_bus.receive("dev")]
        assert len(msgs) == 0
        assert len(tm_registry._pending_replies) == 0
