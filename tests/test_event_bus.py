"""Unit tests for EventBus incremental read methods."""
import pytest
from arf.core.events import AgentEvent
from arf.event_bus import InMemoryEventBus


class TestEventBusIncremental:
    @pytest.fixture
    def bus(self):
        return InMemoryEventBus()

    def test_event_count_starts_zero(self, bus):
        assert bus.event_count() == 0

    def test_event_count_increments(self, bus):
        bus.emit(AgentEvent(type="user_input", data={}))
        bus.emit(AgentEvent(type="error", data={}))
        assert bus.event_count() == 2

    def test_events_since_returns_new_events(self, bus):
        bus.emit(AgentEvent(type="user_input", data={"n": 1}))
        mark = bus.event_count()
        assert mark == 1
        bus.emit(AgentEvent(type="error", data={"n": 2}))
        bus.emit(AgentEvent(type="tool_call_start", data={"tool_name": "x"}))
        new = bus.events_since(mark)
        assert len(new) == 2
        assert new[0].data["n"] == 2
        assert new[1].type == "tool_call_start"

    def test_events_since_empty_when_none(self, bus):
        bus.emit(AgentEvent(type="user_input", data={}))
        new = bus.events_since(bus.event_count())
        assert new == []

    def test_events_since_does_not_mutate(self, bus):
        """events_since must not clear or reset internal state."""
        bus.emit(AgentEvent(type="user_input", data={}))
        bus.events_since(0)
        assert bus.event_count() == 1
