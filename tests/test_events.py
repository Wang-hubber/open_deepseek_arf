from typing import get_args

import pytest

from arf.core.events import AgentEvent, EventType


class TestAgentEvent:
    def test_construct_with_type_and_data(self):
        event = AgentEvent(
            type="error",
            data={"detail": "test error", "code": 500},
        )
        assert event.type == "error"
        assert event.data["detail"] == "test error"
        assert event.data["code"] == 500

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
    def test_contains_session_lifecycle(self):
        types = get_args(EventType)
        assert "session_start" in types
        assert "session_end" in types

    def test_contains_guard_events(self):
        types = get_args(EventType)
        assert "guard_block" in types
        assert "guard_pass" in types

    def test_contains_rollback_events(self):
        types = get_args(EventType)
        assert "rollback_executed" in types
        assert "undo_executed" in types

    def test_event_type_count(self):
        """Break-glass: if new event types are added, trace_viewer must be updated."""
        types = get_args(EventType)
        assert len(types) == 27

    def test_contains_protection_events(self):
        types = get_args(EventType)
        for event in ("rate_limited", "circuit_opened", "circuit_half_open",
                       "circuit_closed", "breaker_blocked"):
            assert event in types, f"Missing {event}"


class TestProtectionEventShapes:
    def test_rate_limited_event_shape(self):
        event = AgentEvent(
            type="rate_limited",
            data={"model": "deep", "api_base": "https://api.deepseek.com"},
        )
        assert event.data["model"] == "deep"

    def test_circuit_opened_event_shape(self):
        event = AgentEvent(
            type="circuit_opened",
            data={"model": "deep", "failure_count": 3, "fail_reason": "500 error"},
        )
        assert event.data["failure_count"] == 3

    def test_circuit_half_open_event_shape(self):
        event = AgentEvent(
            type="circuit_half_open",
            data={"model": "deep", "open_duration_ms": 10000},
        )
        assert event.data["open_duration_ms"] == 10000

    def test_circuit_closed_event_shape(self):
        event = AgentEvent(type="circuit_closed", data={"model": "deep"})
        assert event.data["model"] == "deep"

    def test_breaker_blocked_event_shape(self):
        event = AgentEvent(
            type="breaker_blocked",
            data={"model": "deep", "circuit_state": "open"},
        )
        assert event.data["circuit_state"] == "open"


class TestRollbackEventShape:
    """Trace viewer and downstream consumers expect this data shape."""

    def test_rollback_executed_event(self):
        event = AgentEvent(
            type="rollback_executed",
            data={
                "turn": 3,
                "rolled_back": [
                    {"name": "file_writer", "rollback_error": None},
                    {"name": "resource_scaffold", "rollback_error": "cannot undo"},
                ],
                "success": False,
            },
        )
        assert event.type == "rollback_executed"
        assert event.data["turn"] == 3
        assert len(event.data["rolled_back"]) == 2
        assert event.data["rolled_back"][0]["name"] == "file_writer"
        assert event.data["rolled_back"][0]["rollback_error"] is None
        assert event.data["rolled_back"][1]["rollback_error"] == "cannot undo"
        assert event.data["success"] is False

    def test_rollback_executed_empty_list(self):
        """Rollback event may have empty rolled_back if all rollbacks failed quickly."""
        event = AgentEvent(
            type="rollback_executed",
            data={"turn": 1, "rolled_back": [], "success": True},
        )
        assert event.data["rolled_back"] == []

    def test_undo_executed_event(self):
        event = AgentEvent(
            type="undo_executed",
            data={
                "from_round": 2,
                "to_round": 1,
                "steps": 1,
                "agent_trace": ["main", "sys_agent"],
            },
        )
        assert event.type == "undo_executed"
        assert event.data["from_round"] == 2
        assert event.data["to_round"] == 1
        assert event.data["steps"] == 1

    def test_tool_call_end_carries_rollback_fields(self):
        """tool_call_end must carry rolled_back and rollback_error for viewer rendering."""
        # Simulate the data dict that GraphEngine._emit passes
        data = {
            "tool_name": "file_writer",
            "success": False,
            "duration_ms": 42,
            "result": "",
            "error": "disk full",
            "rolled_back": True,
            "rollback_error": "cleanup failed",
        }
        event = AgentEvent(type="tool_call_end", data=data)
        assert event.data["rolled_back"] is True
        assert event.data["rollback_error"] == "cleanup failed"
        assert event.data["success"] is False

    def test_tool_call_end_no_rollback(self):
        """Normal successful tool call has no rollback fields set."""
        data = {
            "tool_name": "file_reader",
            "success": True,
            "duration_ms": 12,
            "result": '{"content": "hello"}',
            "error": "",
            "rolled_back": False,
            "rollback_error": None,
        }
        event = AgentEvent(type="tool_call_end", data=data)
        assert event.data["rolled_back"] is False
        assert event.data["rollback_error"] is None
