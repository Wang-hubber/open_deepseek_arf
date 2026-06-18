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
