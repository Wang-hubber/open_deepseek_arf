"""Tests for TaskLifecycleProtocol and DefaultTaskLifecycle."""
import pytest
from unittest.mock import MagicMock
from arf.core.protocols.task_lifecycle import TaskLifecycleProtocol, DefaultTaskLifecycle
from arf.core.plugin_context import PluginContext


class TestTaskLifecycleProtocol:
    def test_protocol_is_runtime_checkable(self):
        assert isinstance(DefaultTaskLifecycle(MagicMock()), TaskLifecycleProtocol)


class TestDefaultTaskLifecycle:
    @pytest.mark.anyio
    async def test_complete_emits_event_with_all_fields(self):
        event_bus = MagicMock()
        lifecycle = DefaultTaskLifecycle(event_bus)
        ctx = PluginContext(
            session_id="s1", interaction_round=5,
            state={"session_id": "s1", "messages": [], "_task_start_round": 1},
            event_bus=event_bus,
        )

        result = await lifecycle.complete(
            result="done", files_changed={"modified": ["app.py"]},
            confidence=0.9, notes="all tests pass", ctx=ctx,
        )

        assert result["status"] == "completed"
        assert "task_id" in result

        event = event_bus.emit.call_args[0][0]
        assert event.type == "task_completed"
        assert event.data["session_id"] == "s1"
        assert event.data["start_round"] == 1
        assert event.data["finish_round"] == 5
        assert event.data["result"] == "done"
        assert event.data["confidence"] == 0.9
        assert event.data["notes"] == "all tests pass"

    @pytest.mark.anyio
    async def test_complete_defaults_start_round_to_zero(self):
        event_bus = MagicMock()
        lifecycle = DefaultTaskLifecycle(event_bus)
        ctx = PluginContext(
            session_id="s1", interaction_round=1,
            state={"session_id": "s1", "messages": []},
            event_bus=event_bus,
        )
        await lifecycle.complete(result="x", files_changed={},
                                 confidence=1.0, notes="", ctx=ctx)
        event = event_bus.emit.call_args[0][0]
        assert event.data["start_round"] == 0
