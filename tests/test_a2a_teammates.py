"""Tests for A2A teammate protocols."""
import pytest

from arf.core.protocols.communication import AgentMessage


def test_agent_message_default_priority():
    """AgentMessage should default to normal priority."""
    msg = AgentMessage(
        sender="PM",
        receiver="Dev",
        type="info",
        payload={"task": "build API"},
    )
    assert msg.priority == "normal"


def test_agent_message_urgent_priority():
    """AgentMessage should accept urgent priority."""
    msg = AgentMessage(
        sender="PM",
        receiver="Dev",
        type="info",
        payload={"task": "fix critical bug"},
        priority="urgent",
    )
    assert msg.priority == "urgent"


def test_agent_message_fields_roundtrip():
    """All fields should survive round-trip construction."""
    msg = AgentMessage(
        sender="PM",
        receiver="Dev",
        type="info",
        payload={"task": "build"},
        priority="normal",
        reply_to="msg_001",
        correlation_id="corr_001",
    )
    assert msg.sender == "PM"
    assert msg.receiver == "Dev"
    assert msg.type == "info"
    assert msg.priority == "normal"
    assert msg.reply_to == "msg_001"
    assert msg.correlation_id == "corr_001"


@pytest.mark.anyio
async def test_agent_bus_register_and_discover():
    """register() adds agent, discover() finds by capability."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="Dev", description="Developer", capabilities=["coding", "tool_building"]))
    await bus.register(AgentInfo(name="Data", description="Data expert", capabilities=["data_query", "coding"]))

    all_agents = await bus.discover()
    assert len(all_agents) == 2

    coders = await bus.discover("coding")
    assert len(coders) == 2

    data_only = await bus.discover("data_query")
    assert len(data_only) == 1
    assert data_only[0].name == "Data"


@pytest.mark.anyio
async def test_agent_bus_send_and_receive():
    """send() delivers to receiver's inbox, receive() consumes it."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="PM", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="Dev", description="Dev", capabilities=[]))

    msg = AgentMessage(
        sender="PM",
        receiver="Dev",
        type="task_request",
        payload={"task": "build API"},
        correlation_id="corr_001",
    )
    await bus.send(msg)

    received = []
    async for m in bus.receive("Dev"):
        received.append(m)

    assert len(received) == 1
    assert received[0].sender == "PM"
    assert received[0].payload["task"] == "build API"
    assert received[0].correlation_id == "corr_001"


@pytest.mark.anyio
async def test_agent_bus_broadcast():
    """receiver=None sends to all registered agents except sender."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="PM", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="Dev", description="Dev", capabilities=[]))
    await bus.register(AgentInfo(name="Data", description="Data", capabilities=[]))

    msg = AgentMessage(
        sender="PM",
        receiver=None,  # broadcast
        type="info",
        payload={"announcement": "meeting at 3pm"},
    )
    await bus.send(msg)

    # PM should NOT receive (sender)
    pm_received = []
    async for m in bus.receive("PM"):
        pm_received.append(m)
    assert len(pm_received) == 0

    # Dev and Data should receive
    dev_received = []
    async for m in bus.receive("Dev"):
        dev_received.append(m)
    assert len(dev_received) == 1

    data_received = []
    async for m in bus.receive("Data"):
        data_received.append(m)
    assert len(data_received) == 1


@pytest.mark.anyio
async def test_agent_bus_receive_is_consuming():
    """receive() drains the inbox — second call returns empty."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="PM", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="Dev", description="Dev", capabilities=[]))

    await bus.send(AgentMessage(sender="PM", receiver="Dev", type="info", payload={}))

    first = [m async for m in bus.receive("Dev")]
    assert len(first) == 1

    second = [m async for m in bus.receive("Dev")]
    assert len(second) == 0


@pytest.fixture
def temp_data_dir(tmp_path):
    """Temporary data directory for SessionIndex."""
    return str(tmp_path / "data")


# ── SessionIndex tests ──────────────────────────────────────────────────────


def test_session_index_parse_session_id():
    """parse_session_id extracts group_id and role from member session IDs."""
    from arf.session.session_index import SessionIndex

    assert SessionIndex.parse_session_id("proj_abc__pm") == ("proj_abc", "pm")
    assert SessionIndex.parse_session_id("proj_abc__dev") == ("proj_abc", "dev")
    assert SessionIndex.parse_session_id("plain_session") is None
    assert SessionIndex.parse_session_id("proj_abc__pm--task_1") is None  # sub-agent


@pytest.mark.anyio
async def test_session_index_create_and_load(temp_data_dir):
    """create() writes the index file, load() reads it back."""
    from arf.session.session_index import SessionIndex

    idx = SessionIndex(temp_data_dir)
    members = [
        {"role": "pm", "agent_name": "pm_agent", "session_id": "proj_abc__pm", "status": "active"},
        {"role": "dev", "agent_name": "dev_agent", "session_id": "proj_abc__dev", "status": "active"},
    ]
    created = await idx.create("proj_abc", members)
    assert created["group_id"] == "proj_abc"
    assert "created_at" in created
    assert len(created["members"]) == 2

    loaded = await idx.load("proj_abc")
    assert loaded["group_id"] == "proj_abc"
    assert loaded["members"][0]["role"] == "pm"


@pytest.mark.anyio
async def test_session_index_update_member(temp_data_dir):
    """update_member changes a member's fields."""
    from arf.session.session_index import SessionIndex

    idx = SessionIndex(temp_data_dir)
    members = [
        {"role": "pm", "agent_name": "pm_agent", "session_id": "proj_abc__pm", "status": "active"},
        {"role": "dev", "agent_name": "dev_agent", "session_id": "proj_abc__dev", "status": "active"},
    ]
    await idx.create("proj_abc", members)

    await idx.update_member("proj_abc", "dev", {"status": "idle"})

    loaded = await idx.load("proj_abc")
    dev = next(m for m in loaded["members"] if m["role"] == "dev")
    assert dev["status"] == "idle"
    # pm unchanged
    pm = next(m for m in loaded["members"] if m["role"] == "pm")
    assert pm["status"] == "active"


@pytest.mark.anyio
async def test_session_index_child_tasks(temp_data_dir):
    """add_child_task and update_child_status manage sub-agent tasks."""
    from arf.session.session_index import SessionIndex

    idx = SessionIndex(temp_data_dir)
    members = [
        {"role": "dev", "agent_name": "dev_agent", "session_id": "proj_abc__dev", "status": "active"},
    ]
    await idx.create("proj_abc", members)

    task = {"task_id": "task_1", "child_session_id": "proj_abc__dev--task_1", "status": "running"}
    await idx.add_child_task("proj_abc", "dev", task)

    loaded = await idx.load("proj_abc")
    dev = next(m for m in loaded["members"] if m["role"] == "dev")
    assert len(dev.get("child_tasks", [])) == 1
    assert dev["child_tasks"][0]["status"] == "running"

    # Update status
    await idx.update_child_status("proj_abc", "dev", "proj_abc__dev--task_1", "completed")
    loaded = await idx.load("proj_abc")
    dev = next(m for m in loaded["members"] if m["role"] == "dev")
    assert dev["child_tasks"][0]["status"] == "completed"


@pytest.mark.anyio
async def test_session_index_load_nonexistent(temp_data_dir):
    """load() returns None for nonexistent group."""
    from arf.session.session_index import SessionIndex

    idx = SessionIndex(temp_data_dir)
    result = await idx.load("nonexistent")
    assert result is None


# ── PeerTeamConfig tests ───────────────────────────────────────────────────────
from arf.plugins.a2a_teammates.config import PeerTeamConfig, MemberConfig


def test_peer_team_config_default_group_id():
    """group_id should be optional with a sensible default."""
    cfg = PeerTeamConfig(members=[
        MemberConfig(role="pm", agent_name="pm_agent", entry_point=True),
    ])
    assert cfg.group_id == "default" or cfg.group_id


def test_peer_team_config_explicit_group_id():
    """Explicit group_id should be preserved."""
    cfg = PeerTeamConfig(
        group_id="proj_abc",
        members=[
            MemberConfig(role="pm", agent_name="pm_agent", entry_point=True),
            MemberConfig(role="dev", agent_name="dev_agent"),
            MemberConfig(role="data", agent_name="data_agent"),
        ],
    )
    assert cfg.group_id == "proj_abc"
    assert len(cfg.members) == 3
    assert cfg.members[0].entry_point is True
    assert cfg.members[1].entry_point is False  # default


def test_peer_team_config_member_default_entry_point():
    """entry_point should default to False."""
    m = MemberConfig(role="dev", agent_name="dev_agent")
    assert m.entry_point is False


# ── send_peer_message tool tests ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_send_peer_message_routes_to_receiver():
    """send_peer_message should deliver a message to the target's inbox."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo
    from arf.plugins.a2a_teammates.tools.send_peer_message.function import execute
    from arf.plugins.a2a_teammates.tools import _registry as teammates_registry

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="pm", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    result = await execute(
        receiver="dev",
        message="Please build the login page",
        type="task_request",
        priority="normal",
        session_id="proj_abc__pm",
    )
    assert result["ok"] is True
    assert result["correlation_id"]

    # Verify message landed in dev's inbox
    received = [m async for m in bus.receive("dev")]
    assert len(received) == 1
    assert received[0].sender == "pm"
    assert received[0].payload["message"] == "Please build the login page"
    assert received[0].type == "task_request"
    assert received[0].priority == "normal"


# ── PeerTeamPlugin tests ────────────────────────────────────────────────────
from arf.plugins.a2a_teammates.plugin import PeerTeamPlugin
from arf.plugins.a2a_teammates.tools import _registry as teammates_registry
from arf.session.session_index import SessionIndex
from arf.core.plugin_context import PluginContext


@pytest.fixture(autouse=True)
def reset_teammates_registry():
    """Reset teammates registry before each test."""
    teammates_registry.agent_bus = None
    yield
    teammates_registry.agent_bus = None


def test_peer_team_plugin_name_and_hooks():
    """Plugin should have correct name and hooks."""
    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
    ]})
    assert plugin.name == "a2a_teammates"
    hooks = plugin.hooks
    assert "session_start" in hooks
    assert "pre_action" in hooks
    assert "session_end" in hooks


@pytest.mark.anyio
async def test_peer_team_plugin_creates_session_index(temp_data_dir):
    """session_start hook should create SessionIndex for the group."""
    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    ctx = PluginContext(
        session_id="proj_abc__pm",
        state={"session_id": "proj_abc__pm", "messages": []},
        data_dir=temp_data_dir,
    )
    await plugin._on_session_start(ctx)

    idx = SessionIndex(temp_data_dir)
    loaded = await idx.load("proj_abc")
    assert loaded is not None
    assert len(loaded["members"]) == 2
    assert loaded["members"][0]["session_id"] == "proj_abc__pm"


@pytest.mark.anyio
async def test_peer_team_plugin_injects_messages(temp_data_dir):
    """pre_action hook should inject pending peer messages into message list."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo, AgentMessage

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="pm", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    # Send a message to dev
    await bus.send(AgentMessage(
        sender="pm", receiver="dev", type="task_request",
        payload={"message": "build API"},
        correlation_id="corr_001",
    ))

    # pre_action on Dev's session
    ctx = PluginContext(
        session_id="proj_abc__dev",
        state={"session_id": "proj_abc__dev", "messages": []},
        current_step="call_model",
        data_dir=temp_data_dir,
    )
    await plugin._on_pre_action(ctx)

    msgs = ctx.state.get("messages", [])
    assert len(msgs) == 1
    assert "pm" in msgs[0]["content"]
    assert "dev" in msgs[0]["content"]
    assert "build API" in msgs[0]["content"]
    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool_call_id"] == "corr_001"


@pytest.mark.anyio
async def test_peer_team_plugin_session_end_updates_status(temp_data_dir):
    """session_end should update member status to 'ended'."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="pm", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    # Create group first
    ctx = PluginContext(
        session_id="proj_abc__pm",
        state={"session_id": "proj_abc__pm", "messages": []},
        data_dir=temp_data_dir,
    )
    await plugin._on_session_start(ctx)

    # End dev's session
    ctx_dev = PluginContext(
        session_id="proj_abc__dev",
        state={"session_id": "proj_abc__dev", "messages": []},
        data_dir=temp_data_dir,
    )
    await plugin._on_session_end(ctx_dev)

    idx = SessionIndex(temp_data_dir)
    loaded = await idx.load("proj_abc")
    dev = next(m for m in loaded["members"] if m["role"] == "dev")
    assert dev["status"] == "ended"
    pm = next(m for m in loaded["members"] if m["role"] == "pm")
    assert pm["status"] == "active"  # unchanged


@pytest.mark.anyio
async def test_peer_team_plugin_resume_group(temp_data_dir):
    """resume_group should re-register members and return the index."""
    from arf.communication.agent_bus import InMemoryAgentBus

    bus = InMemoryAgentBus()
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    # Create group
    ctx = PluginContext(
        session_id="proj_abc__pm",
        state={"session_id": "proj_abc__pm", "messages": []},
        data_dir=temp_data_dir,
    )
    await plugin._on_session_start(ctx)

    # Reset bus — simulate process restart
    new_bus = InMemoryAgentBus()
    teammates_registry.agent_bus = new_bus

    idx = await plugin.resume_group("proj_abc__pm", temp_data_dir)
    assert idx is not None
    assert idx["group_id"] == "proj_abc"

    # Members should be re-registered on new bus
    agents = await new_bus.discover()
    assert len(agents) >= 2


@pytest.mark.anyio
async def test_full_peer_create_and_resume(temp_data_dir):
    """Full lifecycle: create group, send messages, resume from index."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo, AgentMessage

    # Setup: create group
    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
        {"role": "data", "agent_name": "data_agent"},
    ]})

    # PM's session starts → creates index
    ctx_pm = PluginContext(
        session_id="proj_abc__pm",
        state={"session_id": "proj_abc__pm", "messages": []},
        data_dir=temp_data_dir,
    )
    await plugin._on_session_start(ctx_pm)

    # Verify index created
    idx = SessionIndex(temp_data_dir)
    index = await idx.load("proj_abc")
    assert index is not None
    assert len(index["members"]) == 3

    # Send peer messages (lowercase to match plugin's AgentBus registration)
    bus = teammates_registry.agent_bus
    await bus.send(AgentMessage(sender="pm", receiver="dev", type="task_request",
                                 payload={"message": "build the dashboard"},
                                 correlation_id="corr_001", priority="normal"))
    await bus.send(AgentMessage(sender="pm", receiver="data", type="query",
                                 payload={"message": "what tables have user data?"},
                                 correlation_id="corr_002", priority="normal"))

    # Dev's pre_action → injects messages
    ctx_dev = PluginContext(
        session_id="proj_abc__dev",
        state={"session_id": "proj_abc__dev", "messages": []},
        current_step="call_model",
        data_dir=temp_data_dir,
    )
    await plugin._on_pre_action(ctx_dev)
    assert len(ctx_dev.state["messages"]) == 1
    assert "build the dashboard" in ctx_dev.state["messages"][0]["content"]

    # Data's pre_action → injects messages
    ctx_data = PluginContext(
        session_id="proj_abc__data",
        state={"session_id": "proj_abc__data", "messages": []},
        current_step="call_model",
        data_dir=temp_data_dir,
    )
    await plugin._on_pre_action(ctx_data)
    assert len(ctx_data.state["messages"]) == 1
    assert "user data" in ctx_data.state["messages"][0]["content"]

    # Session end → update status
    await plugin._on_session_end(ctx_dev)
    loaded = await idx.load("proj_abc")
    dev = next(m for m in loaded["members"] if m["role"] == "dev")
    assert dev["status"] == "ended"

    # Resume group
    bus2 = InMemoryAgentBus()
    teammates_registry.agent_bus = bus2
    index2 = await plugin.resume_group("proj_abc__pm", temp_data_dir)
    assert index2 is not None
    assert index2["group_id"] == "proj_abc"


@pytest.mark.anyio
async def test_send_peer_message_broadcast(temp_data_dir):
    """receiver=None should broadcast to all peers except sender."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo, AgentMessage

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="PM", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="Dev", description="Dev", capabilities=[]))
    await bus.register(AgentInfo(name="Data", description="Data", capabilities=[]))

    msg = AgentMessage(
        sender="PM", receiver=None, type="info",
        payload={"message": "team meeting at 3pm"},
        correlation_id="corr_003",
    )
    await bus.send(msg)

    # PM should NOT receive own broadcast
    pm_msgs = [m async for m in bus.receive("PM")]
    assert len(pm_msgs) == 0

    # Others should
    dev_msgs = [m async for m in bus.receive("Dev")]
    assert len(dev_msgs) == 1
    data_msgs = [m async for m in bus.receive("Data")]
    assert len(data_msgs) == 1


@pytest.mark.anyio
async def test_parse_session_id_rejects_sub_agents(temp_data_dir):
    """SessionIndex.parse_session_id should reject sub-agent session IDs."""
    assert SessionIndex.parse_session_id("proj_abc__dev--task_1") is None
    assert SessionIndex.parse_session_id("parent--task_1") is None
    assert SessionIndex.parse_session_id("plain") is None


@pytest.mark.anyio
async def test_message_format_includes_priority(temp_data_dir):
    """Formatted peer message should include priority prefix for urgent."""
    from arf.core.protocols.communication import AgentMessage

    normal_msg = AgentMessage(
        sender="PM", receiver="Dev", type="task_request",
        payload={"message": "normal task"},
        correlation_id="c1", priority="normal",
    )
    formatted = PeerTeamPlugin._format_peer_message(normal_msg)
    assert not formatted.startswith("[URGENT]")
    assert "normal task" in formatted

    urgent_msg = AgentMessage(
        sender="PM", receiver="Dev", type="task_request",
        payload={"message": "fix critical bug"},
        correlation_id="c2", priority="urgent",
    )
    formatted = PeerTeamPlugin._format_peer_message(urgent_msg)
    assert formatted.startswith("[URGENT]")
    assert "fix critical bug" in formatted


@pytest.mark.anyio
async def test_pre_action_skips_non_call_model_step(temp_data_dir):
    """pre_action should only inject on call_model step, not execute_tools."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo, AgentMessage

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="PM", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="Dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent"},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    # Send a message
    await bus.send(AgentMessage(
        sender="PM", receiver="Dev", type="task_request",
        payload={"message": "hello"}, correlation_id="c1",
    ))

    # pre_action on execute_tools step — should NOT inject
    ctx = PluginContext(
        session_id="proj_abc__dev",
        state={"session_id": "proj_abc__dev", "messages": []},
        current_step="execute_tools",
        data_dir=temp_data_dir,
    )
    await plugin._on_pre_action(ctx)
    assert len(ctx.state.get("messages", [])) == 0


# ── Reply capture via hook tests ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_round_end_forwards_reply(temp_data_dir):
    """round_end hook should forward last assistant message to pending senders."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo, AgentMessage

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="pm", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    # Simulate: dev received a message from pm, processed it, produced reply
    ctx = PluginContext(
        session_id="proj_abc__dev",
        state={
            "session_id": "proj_abc__dev",
            "messages": [
                {"role": "system", "content": "[Team Communication] ..."},
                {"role": "user", "content": "[Peer message from pm]\nType: task_request\n\nbuild API"},
                {"role": "assistant", "content": "API built successfully with 3 endpoints."},
            ],
            "_pending_peer_reply": [{"sender": "pm", "correlation_id": "corr_001"}],
        },
        data_dir=temp_data_dir,
    )
    await plugin.on_hook("round_end", ctx)

    # Reply should be in pm's inbox
    replies = [m async for m in bus.receive("pm")]
    assert len(replies) == 1
    assert replies[0].sender == "dev"
    assert replies[0].receiver == "pm"
    assert replies[0].type == "answer"
    assert "API built successfully" in replies[0].payload["message"]

    # Flag should be cleared
    assert "_pending_peer_reply" not in ctx.state


@pytest.mark.anyio
async def test_task_completed_forwards_reply(temp_data_dir):
    """task_completed hook should forward reply same as round_end."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo, AgentMessage

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="pm", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    ctx = PluginContext(
        session_id="proj_abc__dev",
        state={
            "session_id": "proj_abc__dev",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "Task done."},
            ],
            "_pending_peer_reply": [{"sender": "pm", "correlation_id": "corr_001"}],
        },
        data_dir=temp_data_dir,
    )
    await plugin.on_hook("task_completed", ctx)

    replies = [m async for m in bus.receive("pm")]
    assert len(replies) == 1
    assert "Task done" in replies[0].payload["message"]
    assert "_pending_peer_reply" not in ctx.state


@pytest.mark.anyio
async def test_round_end_skips_without_pending_reply(temp_data_dir):
    """round_end should be a no-op when there's no _pending_peer_reply."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="pm", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent", "entry_point": True},
        {"role": "dev", "agent_name": "dev_agent"},
    ]})

    ctx = PluginContext(
        session_id="proj_abc__dev",
        state={
            "session_id": "proj_abc__dev",
            "messages": [
                {"role": "user", "content": "standalone task"},
                {"role": "assistant", "content": "done"},
            ],
        },
        data_dir=temp_data_dir,
    )
    # Should not raise, should not send anything
    await plugin.on_hook("round_end", ctx)

    replies = [m async for m in bus.receive("pm")]
    assert len(replies) == 0


@pytest.mark.anyio
async def test_multiple_senders_reply_to_all(temp_data_dir):
    """Reply should be forwarded to all pending senders."""
    from arf.communication.agent_bus import InMemoryAgentBus
    from arf.core.protocols.communication import AgentInfo, AgentMessage

    bus = InMemoryAgentBus()
    await bus.register(AgentInfo(name="pm", description="PM", capabilities=[]))
    await bus.register(AgentInfo(name="data", description="Data", capabilities=[]))
    await bus.register(AgentInfo(name="dev", description="Dev", capabilities=[]))
    teammates_registry.agent_bus = bus

    plugin = PeerTeamPlugin({"group_id": "proj_abc", "members": [
        {"role": "pm", "agent_name": "pm_agent"},
        {"role": "dev", "agent_name": "dev_agent"},
        {"role": "data", "agent_name": "data_agent"},
    ]})

    ctx = PluginContext(
        session_id="proj_abc__dev",
        state={
            "session_id": "proj_abc__dev",
            "messages": [
                {"role": "assistant", "content": "Here is the report for both of you."},
            ],
            "_pending_peer_reply": [
                {"sender": "pm", "correlation_id": "c1"},
                {"sender": "data", "correlation_id": "c2"},
            ],
        },
        data_dir=temp_data_dir,
    )
    await plugin.on_hook("round_end", ctx)

    pm_replies = [m async for m in bus.receive("pm")]
    assert len(pm_replies) == 1
    assert "report" in pm_replies[0].payload["message"]

    data_replies = [m async for m in bus.receive("data")]
    assert len(data_replies) == 1
    assert "report" in data_replies[0].payload["message"]
