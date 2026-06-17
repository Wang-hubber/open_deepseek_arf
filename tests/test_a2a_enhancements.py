"""Tests for A2A Plugin enhancements: HITL, depth limit, conflict detection."""
import asyncio
from unittest.mock import MagicMock

import pytest

from arf.communication.queued_delegator import QueuedTaskDelegator
from arf.core.plugin_context import PluginContext
from arf.plugins.a2a.tools import _registry as a2a_registry
from arf.skills import ask_user_tool


class TestAskUser:
    @pytest.mark.anyio
    async def test_ask_user_returns_pending(self):
        result = await ask_user_tool.execute(
            question="方案A还是B?", options=["A", "B"]
        )
        assert result["ok"] is True
        assert result["pending"] is True
        assert result["question"] == "方案A还是B?"
        assert result["options"] == ["A", "B"]

    @pytest.mark.anyio
    async def test_ask_user_options_defaults_to_empty(self):
        result = await ask_user_tool.execute(question="任意回答?")
        assert result["ok"] is True
        assert result["options"] == []


class TestHITLRoundEnd:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_round_end_detects_human_decision(self):
        """round_end with _pending_human_decision emits human_decision_required."""
        from arf.plugins.a2a.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_hitl"
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "test"}, runner)
        await asyncio.sleep(0)
        task_id = r["task_id"]
        child_sid = f"{parent_sid}--{task_id}"

        child_ctx = PluginContext(
            session_id=child_sid,
            state={
                "session_id": child_sid,
                "messages": [
                    {"role": "user", "content": "do task"},
                    {"role": "assistant", "content": "I need help deciding..."},
                ],
                "current_turn": 2,
                "_pending_human_decision": {
                    "question": "选方案A还是B?",
                    "options": ["A", "B"],
                },
            },
            event_bus=MagicMock(),
        )

        await plugin.on_hook("round_end", child_ctx)

        # Verify human_decision_required event was emitted
        child_ctx.event_bus.emit.assert_called_once()
        event = child_ctx.event_bus.emit.call_args[0][0]
        assert event.type == "human_decision_required"
        assert event.data["question"] == "选方案A还是B?"
        assert event.data["options"] == ["A", "B"]
        assert event.data["child_session_id"] == child_sid

    @pytest.mark.anyio
    async def test_round_end_normal_when_no_decision(self):
        """round_end without _pending_human_decision completes normally."""
        from arf.plugins.a2a.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_normal"
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "test"}, runner)
        await asyncio.sleep(0)
        task_id = r["task_id"]

        child_ctx = PluginContext(
            session_id=f"{parent_sid}--{task_id}",
            state={
                "session_id": f"{parent_sid}--{task_id}",
                "messages": [
                    {"role": "user", "content": "do task"},
                    {"role": "assistant", "content": "Done."},
                ],
                "current_turn": 1,
            },
            event_bus=MagicMock(),
        )

        await plugin.on_hook("round_end", child_ctx)

        # Normal completion — task_completed event
        child_ctx.event_bus.emit.assert_called_once()
        event = child_ctx.event_bus.emit.call_args[0][0]
        assert event.type == "task_completed"
