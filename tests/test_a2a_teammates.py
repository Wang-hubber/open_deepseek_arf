"""Tests for A2A teammate protocols."""
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
