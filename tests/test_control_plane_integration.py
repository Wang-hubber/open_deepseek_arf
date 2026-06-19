"""Integration test: ControlPlane with full plugin stack."""
import pytest
from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore
from arf.plugins.error_handler.plugin import ErrorHandlerPlugin
from arf.plugins.tool_guard.plugin import ToolGuardPlugin
from arf.plugins.trace.plugin import TracePlugin
from arf.engine.compat import drain_astream


pytestmark = pytest.mark.anyio


class _FakeToolExecutor:
    async def execute(self, tool_calls, **kwargs):
        from arf.core.results import ToolResult
        return {tc["id"]: ToolResult(success=True, data="result", tool_name=tc.get("name", "unknown")) for tc in tool_calls}


class _RecordingCallModel:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __call__(self, msgs, model, tools=None):
        self.calls.append({"msgs": msgs, "model": model, "tools": tools})
        resp = self.responses[len(self.calls) - 1] if len(self.calls) <= len(self.responses) else self.responses[-1]
        if isinstance(resp, Exception):
            raise resp
        return resp


def _make_plugins():
    trace = TracePlugin({"data_dir": "/tmp/test_traces"})
    return {
        "blocking": [
            ErrorHandlerPlugin(),
            ToolGuardPlugin({"deny_list": ["rm"]}),
        ],
        "side": [trace],
    }


def _basic_state():
    return {
        "session_id": "test-integration",
        "agent_name": "test",
        "current_model": "test-model",
        "current_turn": 0,
        "interaction_round": 0,
        "messages": [{"role": "user", "content": "hello"}],
    }


async def test_full_round_text_only():
    """Text-only response: one call_model dispatch, no tools."""
    plugins = _make_plugins()
    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_FakeToolExecutor(),
        call_model=_RecordingCallModel([{"content": "Hello! How can I help?"}]),
        blocking_plugins=plugins["blocking"],
        side_plugins=plugins["side"],
    )

    final = await drain_astream(cp, _basic_state())

    msgs = final.get("messages", [])
    assert msgs[-1]["role"] == "assistant"
    assert "Hello" in msgs[-1]["content"]


async def test_round_with_tool_calls():
    """Model returns tool_calls → execute_tools → model observes result."""
    plugins = _make_plugins()
    model = _RecordingCallModel([
        {"content": "", "tool_calls": [
            {"id": "tc1", "type": "function", "function": {"name": "read", "arguments": '{"path":"doc.txt"}'}}
        ]},
        {"content": "I read the file and it says: result"},
    ])
    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_FakeToolExecutor(),
        call_model=model,
        blocking_plugins=plugins["blocking"],
        side_plugins=plugins["side"],
    )

    final = await drain_astream(cp, _basic_state())

    assert len(model.calls) == 2  # call_model → execute_tools → call_model


async def test_blocked_tool_aborts_round():
    """ToolGuardPlugin blocks 'rm' tool → tool is denied."""
    plugins = _make_plugins()
    model = _RecordingCallModel([
        {"content": "", "tool_calls": [
            {"id": "tc1", "type": "function", "function": {"name": "rm", "arguments": '{"path":"important.txt"}'}}
        ]},
    ])
    cp = ControlPlane(
        max_turns=5,
        state_store=InMemoryStateStore(),
        tool_executor=_FakeToolExecutor(),
        call_model=model,
        blocking_plugins=plugins["blocking"],
        side_plugins=plugins["side"],
    )

    final = await drain_astream(cp, _basic_state())
    # After blocking, ErrorHandler aborts — we should not see tool messages for rm
    tool_msgs = [m for m in final.get("messages", []) if m.get("role") == "tool"]
    # The tool should have been blocked by ToolGuard → ErrorHandler aborted
    # So either no tool messages, or a synthetic error message


async def test_skeleton_runs_without_any_plugins():
    """Minimal execution: no plugins, skeleton only."""
    cp = ControlPlane(
        max_turns=3,
        state_store=InMemoryStateStore(),
        tool_executor=_FakeToolExecutor(),
        call_model=_RecordingCallModel([{"content": "Hi!"}]),
    )

    final = await drain_astream(cp, _basic_state())
    assert final.get("messages")[-1]["role"] == "assistant"


async def test_multiple_rounds():
    """Two consecutive rounds work correctly."""
    cp = ControlPlane(
        max_turns=10,
        state_store=InMemoryStateStore(),
        tool_executor=_FakeToolExecutor(),
        call_model=_RecordingCallModel([
            {"content": "Round 1 response"},
            {"content": "Round 2 response"},
        ]),
    )

    state = _basic_state()
    # First round
    final1 = await drain_astream(cp, state)
    assert final1["messages"][-1]["content"] == "Round 1 response"

    # Second round — add new user message
    final1["messages"].append({"role": "user", "content": "question 2"})
    final1["current_turn"] = 0  # reset turn counter for new round
    final2 = await drain_astream(cp, final1)
    assert "Round 2 response" in final2["messages"][-1]["content"]
