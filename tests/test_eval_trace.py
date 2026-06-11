"""Unit tests for events_to_trace adapter."""
import pytest
from arf.core.events import AgentEvent
from arf.plugins.eval.trace_adapter import events_to_trace


def make_event(turn, type, **data):
    return AgentEvent(type=type, turn=turn, data=data, timestamp=1000.0)


class TestEventsToTrace:
    def test_empty_events(self):
        assert events_to_trace([]) == {"turns": []}

    def test_single_turn_tool_call(self):
        events = [
            make_event(1, "tool_call_start", tool_name="file_reader"),
            make_event(1, "tool_call_end", tool_name="file_reader",
                       success=True, duration_ms=42,
                       result='{"content":"hello"}', error=""),
        ]
        trace = events_to_trace(events)
        assert len(trace["turns"]) == 1
        t = trace["turns"][0]
        assert t["turn"] == 1
        assert t["error"] is None
        assert len(t["tool_calls"]) == 1
        assert t["tool_calls"][0]["tool_name"] == "file_reader"
        assert t["tool_calls"][0]["success"] is True

    def test_multi_turn_separation(self):
        events = [
            make_event(1, "tool_call_start", tool_name="a"),
            make_event(1, "tool_call_end", tool_name="a", success=True),
            make_event(2, "tool_call_start", tool_name="b"),
            make_event(2, "tool_call_end", tool_name="b", success=False,
                       error="boom"),
        ]
        trace = events_to_trace(events)
        assert len(trace["turns"]) == 2
        assert trace["turns"][0]["turn"] == 1
        assert trace["turns"][1]["turn"] == 2
        assert trace["turns"][1]["tool_calls"][0]["error"] == "boom"

    def test_model_output_captured(self):
        events = [
            make_event(1, "tool_call_start", tool_name="x"),
            make_event(1, "tool_call_end", tool_name="x", success=True),
            make_event(1, "model_call_end", model="deep",
                       content="File created: hello.py",
                       usage={"total_tokens": 150}),
        ]
        trace = events_to_trace(events)
        t = trace["turns"][0]
        assert "File created" in t["model_output"]

    def test_error_event_tracked(self):
        events = [
            make_event(1, "tool_call_start", tool_name="x"),
            make_event(1, "error", detail="connection refused"),
        ]
        trace = events_to_trace(events)
        assert trace["turns"][0]["error"] == "connection refused"

    def test_duration_computed(self):
        events = [
            make_event(1, "tool_call_start", tool_name="x"),
            make_event(1, "tool_call_end", tool_name="x",
                       duration_ms=100),
        ]
        trace = events_to_trace(events)
        assert trace["turns"][0]["duration_ms"] >= 0
