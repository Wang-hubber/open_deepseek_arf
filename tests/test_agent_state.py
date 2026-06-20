"""Tests for AgentState, Message, WaitItem, ModelResult dataclasses."""
import pytest
from arf.agent.state import AgentState, Message, WaitItem, ModelResult


def test_create_empty_agent_state():
    state = AgentState(
        agent_id="test-agent",
        session_id="",
        messages=[],
        waiting={},
        model_config={"api_base": "https://x.com/v1", "api_key_env": "KEY", "model_name": "m1", "context_window": 128000},
    )
    assert state.agent_id == "test-agent"
    assert state.session_id == ""
    assert state.messages == []
    assert state.waiting == {}


def test_message_creation():
    msg = Message(message_id="m1", role="user", content="hello")
    assert msg.message_id == "m1"
    assert msg.role == "user"
    assert msg.content == "hello"


def test_message_content_can_be_dict():
    msg = Message(message_id="m2", role="tool", content={"tool_call_id": "t1", "result": "ok"})
    assert msg.content["result"] == "ok"


def test_wait_item_creation():
    wi = WaitItem(wait_id="w1", hook_name="before_tools", reason="approval")
    assert wi.hook_name == "before_tools"


def test_model_result_creation():
    mr = ModelResult(content="hi", tool_calls=[], usage={}, finish_reason="stop")
    assert mr.content == "hi"
    assert mr.tool_calls == []


def test_model_result_with_tool_calls():
    mr = ModelResult(
        content="",
        tool_calls=[{"id": "t1", "name": "read_file", "params": {"path": "x.txt"}}],
        usage={"total_tokens": 100},
        finish_reason="tool_calls",
    )
    assert len(mr.tool_calls) == 1
    assert mr.tool_calls[0]["name"] == "read_file"
