"""Integration tests for engine primitive detection: task_complete + HITL."""
import json
import pytest
from unittest.mock import MagicMock
from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore
from arf.core.results import ToolResult
from arf.core.plugin_context import PluginContext


class _FakeToolExecutor:
    """Returns tool results that trigger primitive detection."""
    def __init__(self, data: dict):
        self._data = data

    async def execute(self, tool_calls, **kwargs):
        return {tc["id"]: ToolResult(
            success=True,
            data=json.dumps(self._data, ensure_ascii=False),
            tool_name=tc.get("name", "unknown"),
        ) for tc in tool_calls}


class _RecordingCallModel:
    def __init__(self, responses: list):
        self.responses = responses
        self.calls: list = []

    async def __call__(self, msgs, model, tools=None):
        self.calls.append({"msgs": msgs, "model": model, "tools": tools})
        idx = len(self.calls) - 1
        return self.responses[idx] if idx < len(self.responses) else self.responses[-1]


def _basic_state():
    return {
        "session_id": "test-primitive",
        "agent_name": "test",
        "current_model": "test-model",
        "current_turn": 0,
        "interaction_round": 0,
        "messages": [{"role": "user", "content": "do task"}],
        "_task_start_round": 0,
    }


@pytest.mark.anyio
async def test_task_complete_ends_round_and_updates_pointer():
    """kernel__task_complete tool call -> round ends, _task_start_round advances."""
    model = _RecordingCallModel([
        {"content": "", "tool_calls": [
            {"id": "tc1", "type": "function", "function": {
                "name": "kernel__task_complete",
                "arguments": '{"result":"done","confidence":0.95,"notes":"all good"}',
            }}
        ]},
    ])
    cp = ControlPlane(
        max_turns=5, state_store=InMemoryStateStore(),
        tool_executor=_FakeToolExecutor({
            "task_complete": True, "result": "done",
            "confidence": 0.95, "files_changed": {}, "notes": "all good",
        }),
        call_model=model,
    )
    final = await cp.invoke(_basic_state())

    assert len(model.calls) == 1  # round ended after task_complete
    assert "_primitive_result" not in final
    assert final["_task_start_round"] == 2  # finish_round(1) + 1


@pytest.mark.anyio
async def test_pending_human_ends_round_and_sets_state():
    """kernel__ask_user tool -> round ends, _pending_human_decision set."""
    model = _RecordingCallModel([
        {"content": "", "tool_calls": [
            {"id": "tc1", "type": "function", "function": {
                "name": "kernel__ask_user",
                "arguments": '{"question":"A or B?","options":["A","B"]}',
            }}
        ]},
    ])
    cp = ControlPlane(
        max_turns=5, state_store=InMemoryStateStore(),
        tool_executor=_FakeToolExecutor({
            "pending": True, "question": "A or B?", "options": ["A", "B"],
            "context": "", "task_id": "",
        }),
        call_model=model,
    )
    final = await cp.invoke(_basic_state())

    assert len(model.calls) == 1
    assert final["_pending_human_decision"] is not None
    assert final["_pending_human_decision"]["question"] == "A or B?"
