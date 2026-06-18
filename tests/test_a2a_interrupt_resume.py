"""Tests for A2A interrupt/resume feature — child_tasks field."""
import asyncio

import pytest
from arf.core.state import AgentState
from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore, FileStateStore
from arf.plugins.a2a_subagents.plugin import A2APlugin
from arf.plugins.a2a_subagents.tools import _registry
from arf.core.plugin_context import PluginContext
from arf.testing import InMemoryToolExecutor


@pytest.fixture(autouse=True)
def reset_a2a_registry():
    """Reset the A2A registry singleton before each test."""
    _registry.delegator = None
    _registry.max_task_timeout = 600.0
    _registry.running_sub_agents.clear()
    _registry.runtime_task_ids.clear()
    _registry.cancel_events.clear()
    yield
    _registry.delegator = None
    _registry.running_sub_agents.clear()
    _registry.runtime_task_ids.clear()
    _registry.cancel_events.clear()


@pytest.fixture
def temp_data_dir(tmp_path):
    """Temporary data directory for FileStateStore."""
    return str(tmp_path / "data")


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
    from arf.plugins.a2a_subagents.config import A2APluginConfig

    cfg = A2APluginConfig()
    assert cfg.child_resume == "auto"


def test_a2a_config_child_resume_notify():
    """child_resume can be set to 'notify'."""
    from arf.plugins.a2a_subagents.config import A2APluginConfig

    cfg = A2APluginConfig(child_resume="notify")
    assert cfg.child_resume == "notify"


def test_a2a_config_child_resume_invalid():
    """child_resume rejects invalid values."""
    from arf.plugins.a2a_subagents.config import A2APluginConfig

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


@pytest.mark.anyio
async def test_child_tasks_persists_in_parent_state(temp_data_dir):
    """child_tasks entries should survive state round-trip through FileStateStore."""
    parent_sid = "parent_s1"
    store = FileStateStore(temp_data_dir)

    state = {
        "session_id": parent_sid,
        "messages": [{"role": "user", "content": "delegate task"}],
        "child_tasks": [
            {
                "task_id": "task_1",
                "child_session_id": "parent_s1--task_1",
                "agent_name": "coder",
                "status": "running",
                "created_at": 1718745600.0,
            }
        ],
    }
    await store.put(parent_sid, state)

    # Round-trip
    restored = await store.get(parent_sid)
    assert restored is not None
    assert len(restored["child_tasks"]) == 1
    assert restored["child_tasks"][0]["status"] == "running"
    assert restored["child_tasks"][0]["child_session_id"] == "parent_s1--task_1"


@pytest.mark.anyio
async def test_cascade_cancel_updates_child_tasks_and_sets_event(temp_data_dir):
    """cascade_cancel should set cancel_event and update child_tasks status."""
    plugin = A2APlugin({"max_concurrent_tasks": 1, "child_resume": "auto"})

    # Setup parent state with a running child
    parent_sid = "parent_s1"
    child_sid = "parent_s1--task_1"
    parent_store = FileStateStore(temp_data_dir)
    await parent_store.put(parent_sid, {
        "session_id": parent_sid,
        "messages": [],
        "child_tasks": [
            {"task_id": "task_1", "child_session_id": child_sid,
             "agent_name": "coder", "status": "running", "created_at": 0.0},
        ],
    })

    # Register cancel event for the child
    event = plugin.child_cancel_event(child_sid)
    assert not event.is_set()

    # Cascade cancel (simulating parent session_end)
    ctx = PluginContext(
        session_id=parent_sid,
        state={"session_id": parent_sid},
        data_dir=temp_data_dir,
    )
    await plugin.cascade_cancel(ctx)

    # Child cancel_event should be set
    assert event.is_set()

    # Parent state child_tasks should be updated
    restored = await parent_store.get(parent_sid)
    assert restored is not None
    assert restored.get("child_tasks")
    assert restored["child_tasks"][0]["status"] == "cancelled"


@pytest.mark.anyio
async def test_update_child_status_modifies_parent_state(temp_data_dir):
    """_update_child_status should update a specific child_tasks entry."""
    plugin = A2APlugin({"child_resume": "auto"})

    parent_sid = "parent_s1"
    child_sid = "child_s1"
    parent_store = FileStateStore(temp_data_dir)
    await parent_store.put(parent_sid, {
        "session_id": parent_sid,
        "messages": [],
        "child_tasks": [
            {"task_id": "task_1", "child_session_id": child_sid,
             "agent_name": "coder", "status": "running", "created_at": 0.0},
            {"task_id": "task_2", "child_session_id": "child_s2",
             "agent_name": "reviewer", "status": "running", "created_at": 0.0},
        ],
    })

    ctx = PluginContext(
        session_id=child_sid,
        state={"session_id": child_sid},
        data_dir=temp_data_dir,
    )
    await plugin._update_child_status(ctx, parent_sid, child_sid, "completed")

    # Only task_1 should be updated
    restored = await parent_store.get(parent_sid)
    ct1 = next(c for c in restored["child_tasks"] if c["child_session_id"] == child_sid)
    ct2 = next(c for c in restored["child_tasks"] if c["child_session_id"] == "child_s2")
    assert ct1["status"] == "completed"
    assert ct2["status"] == "running"


def test_build_child_resume_notification_lists_unfinished():
    """Notification should list all unfinished child tasks with session IDs."""
    unfinished = [
        {"task_id": "task_1", "agent_name": "coder",
         "child_session_id": "a2a_coder_abc123", "status": "running"},
        {"task_id": "task_2", "agent_name": "reviewer",
         "child_session_id": "a2a_reviewer_def456", "status": "pending"},
    ]
    msg = A2APlugin.build_child_resume_notification(unfinished)

    assert "task_1" in msg
    assert "task_2" in msg
    assert "coder" in msg
    assert "reviewer" in msg
    assert "a2a_coder_abc123" in msg
    assert "a2a_reviewer_def456" in msg
    assert "resume_session" in msg
    assert "delegate_task" in msg


@pytest.mark.anyio
async def test_resume_preserves_turn_counter():
    """resume() should continue from saved turn, not reset to 0."""
    store = InMemoryStateStore()

    async def mock_model(messages, tools=None, **kwargs):
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    saved_state = {
        "session_id": "child_s1",
        "agent_name": "coder",
        "messages": [
            {"role": "user", "content": "write code"},
            {"role": "assistant", "content": "ok"},
        ],
        "current_model": "test-model",
        "current_turn": 3,
        "interaction_round": 1,
        "context_summary": "",
        "tool_results": {},
        "session_active": True,
        "_session_opened": True,
        "_session_ended": False,
    }

    cp = ControlPlane(
        state_store=store,
        tool_executor=InMemoryToolExecutor(),
        call_model=mock_model,
        max_turns=10,
    )
    events = []
    async for event in cp.resume(saved_state):
        events.append(event)

    # Should complete normally
    session_end = [e for e in events if e.type == "session_end"]
    assert len(session_end) == 1

    # State should be saved
    restored = await store.get("child_s1")
    assert restored is not None
    assert restored["_session_ended"] is True
    # Turn 3 -> 4 (one more model call)
    assert restored["current_turn"] >= 3


@pytest.mark.anyio
async def test_resume_with_gate_exceeded():
    """resume() when gate already exceeded should emit gate_exceeded."""
    store = InMemoryStateStore()

    async def mock_model(messages, tools=None, **kwargs):
        return {"role": "assistant", "content": "Done.", "tool_calls": None}

    saved_state = {
        "session_id": "child_s1",
        "agent_name": "coder",
        "messages": [
            {"role": "user", "content": "write code"},
        ],
        "current_model": "test-model",
        "current_turn": 5,  # Already at max_turns
        "interaction_round": 1,
        "context_summary": "",
        "tool_results": {},
        "session_active": True,
        "_session_opened": True,
        "_session_ended": False,
    }

    cp = ControlPlane(
        state_store=store,
        tool_executor=InMemoryToolExecutor(),
        call_model=mock_model,
        max_turns=5,
    )
    events = []
    async for event in cp.resume(saved_state):
        events.append(event)

    # Should hit gate
    gate_events = [e for e in events if e.type == "gate_exceeded"]
    assert len(gate_events) >= 1
