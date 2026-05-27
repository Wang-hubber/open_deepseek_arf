from typing import get_args

from arf.core.events import AgentEvent, EventType


class TestAgentEvent:
    def test_construct_with_type_and_data(self):
        event = AgentEvent(
            type="agent_switch",
            data={"from": "arf_assistant", "to": "sys_agent", "task": "create tool"},
        )
        assert event.type == "agent_switch"
        assert event.data["from"] == "arf_assistant"
        assert event.data["to"] == "sys_agent"

    def test_defaults_are_set(self):
        event = AgentEvent(type="error", data={"msg": "boom"})
        assert event.trace_id == ""
        assert event.span_id == ""
        assert event.parent_span_id is None
        assert event.session_id == ""
        assert event.agent_name == ""
        assert event.turn == 0
        assert event.timestamp > 0


class TestEventType:
    def test_contains_agent_switch(self):
        types = get_args(EventType)
        assert "agent_switch" in types

    def test_contains_session_lifecycle(self):
        types = get_args(EventType)
        assert "session_start" in types
        assert "session_end" in types

    def test_contains_guard_events(self):
        types = get_args(EventType)
        assert "guard_block" in types
        assert "guard_pass" in types
