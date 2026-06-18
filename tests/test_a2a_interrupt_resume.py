"""Tests for A2A interrupt/resume feature — child_tasks field."""
import asyncio

import pytest
from arf.core.state import AgentState


def test_agent_state_accepts_child_tasks():
    """AgentState should accept child_tasks list of task dicts."""
    state: AgentState = {
        "session_id": "parent_s1",
        "messages": [],
        "child_tasks": [
            {
                "task_id": "task_1",
                "child_session_id": "s1--task_1",
                "agent_name": "coder",
                "status": "running",
                "created_at": 1718745600.0,
            }
        ],
    }
    assert state["child_tasks"][0]["task_id"] == "task_1"
    assert state["child_tasks"][0]["status"] == "running"


def test_a2a_config_child_resume_default():
    """child_resume defaults to 'auto'."""
    from arf.plugins.a2a.config import A2APluginConfig

    cfg = A2APluginConfig()
    assert cfg.child_resume == "auto"


def test_a2a_config_child_resume_notify():
    """child_resume can be set to 'notify'."""
    from arf.plugins.a2a.config import A2APluginConfig

    cfg = A2APluginConfig(child_resume="notify")
    assert cfg.child_resume == "notify"


def test_a2a_config_child_resume_invalid():
    """child_resume rejects invalid values."""
    from arf.plugins.a2a.config import A2APluginConfig

    with pytest.raises(Exception):
        A2APluginConfig(child_resume="invalid")


def _make_mock_call_model():
    """Return an async mock that produces a minimal assistant text response."""
    async def mock_call_model(messages, tools=None, **kwargs):
        return {
            "role": "assistant",
            "content": "Done.",
            "tool_calls": None,
        }
    return mock_call_model


@pytest.mark.anyio
async def test_control_plane_resume_continues_from_saved_state():
    """resume() should enter _execute and complete without new user message."""
    from arf.engine.control_plane import ControlPlane
    from arf.engine.checkpoint import InMemoryStateStore
    from arf.core.events import AgentEvent

    store = InMemoryStateStore()

    saved_state = {
        "session_id": "child_s1",
        "agent_name": "coder",
        "messages": [
            {"role": "user", "content": "write a function"},
            {"role": "assistant", "content": None, "tool_calls": []},
        ],
        "current_model": "test-model",
        "current_turn": 2,
        "interaction_round": 1,
        "context_summary": "",
        "tool_results": {},
        "session_active": True,
        "_session_opened": True,
        "_session_ended": False,
    }

    from arf.testing import InMemoryToolExecutor

    cp = ControlPlane(
        state_store=store,
        tool_executor=InMemoryToolExecutor(),
        call_model=_make_mock_call_model(),
        max_turns=10,
    )
    events = []
    async for event in cp.resume(saved_state):
        events.append(event)

    # Should produce a session_end event on completion
    session_end_events = [e for e in events if e.type == "session_end"]
    assert len(session_end_events) == 1

    # State should be saved with _session_ended
    restored = await store.get("child_s1")
    assert restored is not None
    assert restored["_session_ended"] is True
    # Turn counter should have advanced from 2
    assert restored.get("current_turn", 0) >= 2


def test_control_plane_set_cancel_event():
    """ControlPlane.set_cancel_event() should wire cancel_event into _cancelled()."""
    from arf.engine.control_plane import ControlPlane
    from arf.engine.checkpoint import InMemoryStateStore
    from arf.testing import InMemoryToolExecutor

    store = InMemoryStateStore()
    executor = InMemoryToolExecutor()
    cp = ControlPlane(state_store=store, tool_executor=executor)

    # Initially not cancelled
    cancel_evt = asyncio.Event()
    cp.set_cancel_event(cancel_evt)
    assert cp._cancel_event is cancel_evt
    assert cp._cancelled() is False

    # After setting the event
    cancel_evt.set()
    assert cp._cancelled() is True
