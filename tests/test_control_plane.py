"""Tests for ControlPlane — pure skeleton execution loop."""

import asyncio
import pytest
from arf.engine.control_plane import ControlPlane, MessageContractError
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
        max_turns=5,
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
        max_turns=5,
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
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([{"content": "Fallback response"}]),
        stream_model=_failing_stream,
    )

    final = await cp.invoke(state)
    assert final["messages"][-1]["content"] == "Fallback response"


# ============================================================
# MCP error — trace captures failure, execution continues
# ============================================================

@pytest.mark.anyio
async def test_mcp_resolution_failure_emits_error_event():
    """MCP tool resolution failure logs error and emits trace event, continues."""
    from arf.event_bus import InMemoryEventBus

    event_bus = InMemoryEventBus()

    async def _failing_mcp_resolver(state):
        raise RuntimeError("MCP server unreachable")

    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([{"content": "Hi!"}]),
        event_bus=event_bus,
        mcp_tool_resolver=_failing_mcp_resolver,
    )

    final = await cp.invoke(_basic_state())
    assert final["messages"][-1]["content"] == "Hi!"

    errors = event_bus.collected("error")
    assert len(errors) >= 1
    assert "MCP" in errors[0].data.get("detail", "")


@pytest.mark.anyio
async def test_mcp_resolution_success_no_error_event():
    """Normal MCP resolution emits no error events."""
    from arf.event_bus import InMemoryEventBus

    event_bus = InMemoryEventBus()

    async def _ok_mcp_resolver(state):
        return [{"name": "read", "description": "Read a file", "parameters": {}}]

    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([{"content": "Hi!"}]),
        event_bus=event_bus,
        mcp_tool_resolver=_ok_mcp_resolver,
    )

    await cp.invoke(_basic_state())
    errors = event_bus.collected("error")
    mcp_errors = [e for e in errors if "MCP" in e.data.get("detail", "")]
    assert len(mcp_errors) == 0


# ============================================================
# Error handler — trace captures every decision
# ============================================================

@pytest.mark.anyio
async def test_error_handler_abort_emits_trace_event():
    """When error_handler decides abort, trace captures the error with decision.

    Uses a connection error (known transport error type) that the error_handler
    retries and then aborts. Unknown errors re-raise instead of aborting —
    those are tested separately."""
    from arf.event_bus import InMemoryEventBus
    from arf.plugins.error_handler.plugin import ErrorHandlerPlugin
    # Use a connection error so error_handler matches transport strategy
    from arf.core.model_adapter import ModelAdapterError

    event_bus = InMemoryEventBus()

    # ModelAdapterError with status 502 triggers transport retry → abort
    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([
            ModelAdapterError(502, "connection refused")
        ]),
        event_bus=event_bus,
        blocking_plugins=[ErrorHandlerPlugin(max_transport_retry=0)],
    )

    final = await cp.invoke(_basic_state())

    errors = event_bus.collected("error")
    assert len(errors) >= 1
    assert any("no_recovery" in e.data.get("detail", "").lower() for e in errors)


@pytest.mark.anyio
async def test_error_handler_skip_emits_trace_event():
    """Guard denial → skip decision is trace-captured."""
    from arf.event_bus import InMemoryEventBus
    from arf.plugins.error_handler.plugin import ErrorHandlerPlugin

    event_bus = InMemoryEventBus()

    class PermissionDenied(Exception):
        pass

    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([PermissionDenied("tool rm blocked")]),
        event_bus=event_bus,
        blocking_plugins=[ErrorHandlerPlugin()],
    )

    final = await cp.invoke(_basic_state())

    errors = event_bus.collected("error")
    assert len(errors) >= 1
    assert any("noop" in e.data.get("detail", "").lower() for e in errors)


# ============================================================
# abort → SessionAbortedError → invoke cleanup
# ============================================================

@pytest.mark.anyio
async def test_abort_cleans_session_active_flag():
    """When error_handler decides abort, invoke() catches SessionAbortedError
    and sets session_active = False before returning."""
    from arf.core.model_adapter import ModelAdapterError
    from arf.plugins.error_handler.plugin import ErrorHandlerPlugin

    state = _basic_state()

    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([ModelAdapterError(502, "connection timeout")]),
        blocking_plugins=[ErrorHandlerPlugin(max_transport_retry=0)],
    )

    final = await cp.invoke(state)
    assert not final.get("session_active", True)


@pytest.mark.anyio
async def test_abort_returns_partial_state():
    """After abort, invoke() returns the partial state (messages before crash)."""
    from arf.core.model_adapter import ModelAdapterError
    from arf.plugins.error_handler.plugin import ErrorHandlerPlugin

    state = _basic_state()

    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_FakeCallModel([ModelAdapterError(502, "connection timeout")]),
        blocking_plugins=[ErrorHandlerPlugin(max_transport_retry=0)],
    )

    final = await cp.invoke(state)
    # Partial state: session_id and message history preserved
    assert final.get("session_id") == "test"
    assert len(final.get("messages", [])) >= 1


# ============================================================
# Timeout — triggered via error_handler
# ============================================================

@pytest.mark.anyio
async def test_call_timeout_triggers_error_handler_abort():
    """asyncio.TimeoutError from non-streaming call → error_handler → abort."""
    from arf.event_bus import InMemoryEventBus
    from arf.plugins.error_handler.plugin import ErrorHandlerPlugin

    event_bus = InMemoryEventBus()

    async def _hanging_model(msgs, model, tools=None):
        await asyncio.sleep(10.0)

    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_NoopToolExecutor(),
        call_model=_hanging_model,
        event_bus=event_bus,
        call_timeout=0.2,
        blocking_plugins=[ErrorHandlerPlugin(max_transport_retry=0)],
    )

    final = await cp.invoke(_basic_state())

    assert not final.get("session_active", True)
    errors = event_bus.collected("error")
    assert len(errors) >= 1
    assert any("no_recovery" in e.data.get("detail", "").lower() for e in errors)
