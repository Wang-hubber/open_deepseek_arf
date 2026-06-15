"""Integration test: malformed tool call params -> recovery -> model retries."""
import pytest
from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore
from arf.plugins.error_handler.plugin import ErrorHandlerPlugin


@pytest.mark.anyio
async def test_malformed_tool_call_recovers_and_model_sees_error():
    """When tool execution raises, error is injected as tool_result
    and the loop continues so the model can retry."""
    error_handler = ErrorHandlerPlugin()

    # Tool executor that always crashes
    class CrashingToolExecutor:
        async def execute(self, tool_calls, **kwargs):
            raise ValueError("malformed params in tool call")

    cp = ControlPlane(
        state_store=InMemoryStateStore(),
        tool_executor=CrashingToolExecutor(),
        blocking_plugins=[error_handler],
    )

    state = {
        "session_id": "s1",
        "current_turn": 1,
        "interaction_round": 1,
        "messages": [
            {"role": "user", "content": "read file"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc1", "name": "read_file", "params": {"path": None}},
            ]},
        ],
        "_pending_tool_calls": [
            {"id": "tc1", "name": "read_file", "params": {"path": None}},
        ],
    }

    # Collect events from astream
    aborted = False
    try:
        async for event in cp.astream(state):
            if (event.type == "session_end" and
                    event.data.get("reason") == "aborted"):
                aborted = True
    except Exception:
        pass  # expected -- no call_model set, loop will fail after recovery

    # The error should be injected as a tool_result message
    messages = state.get("messages", [])
    tool_results = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_results) >= 1, (
        f"Expected tool_result injected, got messages: {messages}"
    )
    assert "malformed params" in tool_results[0]["content"]

    # Session should NOT have aborted -- recovery kept the loop going
    assert not aborted, "Session should not abort on recoverable tool error"
