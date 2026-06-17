"""Tests for A2A Plugin enhancements: HITL, depth limit, conflict detection."""
import pytest
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
