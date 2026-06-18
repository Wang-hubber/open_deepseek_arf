"""Tests for HITLProtocol and DefaultHITL."""
import pytest
from unittest.mock import MagicMock
from arf.core.protocols.hitl import HITLProtocol, DefaultHITL
from arf.core.plugin_context import PluginContext


class TestHITLProtocol:
    def test_protocol_is_runtime_checkable(self):
        assert isinstance(DefaultHITL(MagicMock(), MagicMock()), HITLProtocol)


class TestDefaultHITL:
    @pytest.mark.anyio
    async def test_request_input_emits_event_and_sets_state_flag(self):
        event_bus = MagicMock()
        hitl = DefaultHITL(event_bus, MagicMock())

        ctx = PluginContext(
            session_id="s1", interaction_round=3,
            state={"session_id": "s1", "messages": []},
            event_bus=event_bus,
        )

        result = await hitl.request_input(
            question="A or B?", options=["A", "B"],
            context="need choice", task_id="t1", deadline=300.0, ctx=ctx,
        )

        assert result["status"] == "pending"
        assert result["request_id"].startswith("s1_3_")

        event = event_bus.emit.call_args[0][0]
        assert event.type == "need_human_input"
        assert event.data["question"] == "A or B?"
        assert event.data["options"] == ["A", "B"]
        assert event.data["context"] == "need choice"
        assert event.data["task_id"] == "t1"
        assert event.data["deadline"] == 300.0

        assert ctx.state["_pending_human_decision"]["question"] == "A or B?"

    @pytest.mark.anyio
    async def test_provide_response_returns_bool(self):
        hitl = DefaultHITL(MagicMock(), MagicMock())
        ctx = PluginContext(session_id="s1", interaction_round=1,
                            state={"session_id": "s1", "messages": []},
                            event_bus=MagicMock())
        result = await hitl.request_input(
            question="?", options=[], context="", task_id="", deadline=60.0, ctx=ctx,
        )
        assert await hitl.provide_response(result["request_id"], "answer") is True
        assert await hitl.provide_response("nonexistent", "x") is False

    @pytest.mark.anyio
    async def test_cancel_request_removes_pending(self):
        hitl = DefaultHITL(MagicMock(), MagicMock())
        ctx = PluginContext(session_id="s1", interaction_round=1,
                            state={"session_id": "s1", "messages": []},
                            event_bus=MagicMock())
        result = await hitl.request_input(
            question="?", options=[], context="", task_id="", deadline=60.0, ctx=ctx,
        )
        assert await hitl.cancel_request(result["request_id"]) is True
        assert await hitl.cancel_request(result["request_id"]) is False
