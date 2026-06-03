"""Tests for ControlPlane — pure skeleton execution loop."""

import asyncio
import pytest
from arf.engine.control_plane import ControlPlane, MessageContractError
from arf.engine.loop_strategies.react import ReActStrategy
from arf.engine.checkpoint import InMemoryStateStore


class _NoopToolExecutor:
    async def execute(self, tool_calls, **kwargs):
        from arf.core.results import ToolResult
        return {tc["id"]: ToolResult(tool_name="test", success=True, data="result") for tc in tool_calls}


class _FakeCallModel:
    def __init__(self, responses=None):
        self.responses = responses or [{"content": "Hello"}]
        self.calls = []

    async def __call__(self, msgs, model, tools=None):
        self.calls.append({"msgs": msgs, "model": model})
        resp = self.responses[len(self.calls) - 1] if len(self.calls) <= len(self.responses) else self.responses[-1]
        if isinstance(resp, Exception):
            raise resp
        return resp


def _basic_state():
    return {
        "session_id": "test",
        "agent_name": "test",
        "current_model": "test-model",
        "current_turn": 0,
        "interaction_round": 0,
        "messages": [{"role": "user", "content": "hello"}],
    }


@pytest.mark.anyio
async def test_skeleton_runs_without_plugins():
    """Skeleton alone can complete a simple round (text-only response)."""
    state = _basic_state()
    cp = ControlPlane(
        loop_strategy=ReActStrategy(max_turns=5),
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([{"content": "Hi there!"}]),
    )

    final = await cp.invoke(state)

    msgs = final.get("messages", [])
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == "Hi there!"


@pytest.mark.anyio
async def test_validate_messages_passes_valid_sequence():
    state = _basic_state()
    state["messages"] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    cp = ControlPlane(
        loop_strategy=ReActStrategy(),
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([{"content": "ok"}]),
    )
    cp._validate_messages(state)  # Should not raise


@pytest.mark.anyio
async def test_validate_messages_rejects_leading_assistant():
    state = _basic_state()
    state["messages"] = [
        {"role": "assistant", "content": "I'm first!",
         "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
    ]
    cp = ControlPlane(
        loop_strategy=ReActStrategy(),
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([]),
    )
    with pytest.raises(MessageContractError, match="start with user"):
        cp._validate_messages(state)


@pytest.mark.anyio
async def test_round_with_tool_calls():
    """Model returns tool_calls -> execute_tools -> model observes result."""
    model = _FakeCallModel([
        {"content": "", "tool_calls": [
            {"id": "tc1", "type": "function", "function": {"name": "read", "arguments": '{"path":"doc.txt"}'}}
        ]},
        {"content": "I read the file and it says: result"},
    ])
    cp = ControlPlane(
        loop_strategy=ReActStrategy(max_turns=5),
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=model,
    )

    final = await cp.invoke(_basic_state())

    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_streaming_fallback_to_non_streaming():
    """When streaming fails, falls back to call_model."""

    async def _failing_stream(msgs, model, tools=None):
        if False:
            yield  # make it a generator (pragma: no cover)
        raise RuntimeError("stream failed")

    state = _basic_state()
    cp = ControlPlane(
        loop_strategy=ReActStrategy(max_turns=5),
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([{"content": "Fallback response"}]),
        stream_model=_failing_stream,
    )

    final = await cp.invoke(state)
    assert final["messages"][-1]["content"] == "Fallback response"
