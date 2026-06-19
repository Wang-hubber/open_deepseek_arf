"""Tests for arf.engine.park_coordinator."""
import asyncio
import pytest
from arf.engine.park_coordinator import ParkCoordinator


class FakeStateStore:
    """In-memory state store for testing."""
    def __init__(self):
        self._store: dict[str, dict] = {}
    async def get(self, sid: str) -> dict | None:
        return self._store.get(sid)
    async def put(self, sid: str, state: dict) -> None:
        self._store[sid] = state


@pytest.fixture
def state():
    return {"session_id": "test", "messages": []}


@pytest.fixture
def coordinator():
    return ParkCoordinator()


# ── register ──

@pytest.mark.anyio
async def test_register_creates_condition_in_state(state, coordinator):
    wid = await coordinator.register(state, "hitl",
        {"request_id": "rid_1", "question": "confirm?"})

    assert wid.startswith("hitl_")
    conds = state["_park_conditions"]
    assert conds[wid]["status"] == "pending"
    assert conds[wid]["type"] == "hitl"
    assert conds[wid]["metadata"]["request_id"] == "rid_1"


@pytest.mark.anyio
async def test_register_state_persistable(state, coordinator):
    wid = await coordinator.register(state, "subagent",
        {"task_id": "task_5", "child_sid": "parent--task_5"})

    # Verify the condition can be serialized (no Event objects in state)
    import json
    encoded = json.dumps(state["_park_conditions"])
    decoded = json.loads(encoded)
    assert decoded[wid]["status"] == "pending"


# ── complete ──

@pytest.mark.anyio
async def test_complete_injects_hitl_result(state, coordinator):
    wid = await coordinator.register(state, "hitl",
        {"request_id": "rid_1"})
    state["_pending_human_decision"] = {"request_id": "rid_1"}
    state["_primitive_result"] = "pending"

    ok = await coordinator.complete(state, wid,
        {"answer": "user says yes"})

    assert ok is True
    assert state["_park_conditions"][wid]["status"] == "completed"
    assert state["messages"][-1] == {"role": "user", "content": "user says yes"}
    assert "_pending_human_decision" not in state
    assert state["_primitive_result"] is None


@pytest.mark.anyio
async def test_complete_injects_subagent_result(state, coordinator):
    wid = await coordinator.register(state, "subagent",
        {"task_id": "task_5"})

    formatted = "[A2A] Task task_5 completed (3 turns):\nResult text here."
    ok = await coordinator.complete(state, wid,
        {"content": formatted, "task_id": "task_5"})

    assert ok is True
    assert state["_park_conditions"][wid]["status"] == "completed"
    assert state["messages"][-1]["role"] == "user"
    assert "task_5" in state["messages"][-1]["content"]


@pytest.mark.anyio
async def test_complete_injects_peer_result(state, coordinator):
    wid = await coordinator.register(state, "peer",
        {"role": "architect"})

    formatted = "[Peer message from architect]\nType: info\n\nHello team"
    ok = await coordinator.complete(state, wid,
        {"content": formatted, "role": "architect"})

    assert ok is True
    assert state["_park_conditions"][wid]["status"] == "completed"
    assert state["messages"][-1]["role"] == "system"
    assert "architect" in state["messages"][-1]["content"]


@pytest.mark.anyio
async def test_complete_nonexistent_wait_id(state, coordinator):
    ok = await coordinator.complete(state, "nonexistent",
        {"answer": "x"})
    assert ok is False


@pytest.mark.anyio
async def test_complete_already_completed(state, coordinator):
    wid = await coordinator.register(state, "hitl", {})
    await coordinator.complete(state, wid, {"answer": "first"})
    ok = await coordinator.complete(state, wid, {"answer": "second"})
    assert ok is False


# ── park_round ──

@pytest.mark.anyio
async def test_park_round_returns_none_without_pending(state, coordinator):
    result = await coordinator.park_round(state)
    assert result is None


@pytest.mark.anyio
async def test_park_round_waits_until_complete(state, coordinator):
    wid = await coordinator.register(state, "hitl", {})

    async def complete_after_delay():
        await asyncio.sleep(0.05)
        await coordinator.complete(state, wid, {"answer": "done"})

    task = asyncio.create_task(complete_after_delay())
    result = await coordinator.park_round(state)
    await task

    assert result == wid


@pytest.mark.anyio
async def test_park_round_responds_to_cancel_event(state, coordinator):
    await coordinator.register(state, "hitl", {})
    cancel = asyncio.Event()

    async def cancel_after_delay():
        await asyncio.sleep(0.05)
        cancel.set()

    asyncio.create_task(cancel_after_delay())
    result = await coordinator.park_round(state, cancel_event=cancel)
    assert result is None


@pytest.mark.anyio
async def test_park_round_first_of_multiple_wins(state, coordinator):
    wid1 = await coordinator.register(state, "hitl", {"n": 1})
    wid2 = await coordinator.register(state, "subagent", {"n": 2})
    wid3 = await coordinator.register(state, "peer", {"n": 3})

    async def complete_wid2():
        await asyncio.sleep(0.03)
        await coordinator.complete(state, wid2, {"content": "task done"})

    asyncio.create_task(complete_wid2())
    result = await coordinator.park_round(state)

    assert result == wid2
    # wid1 and wid3 still pending
    assert state["_park_conditions"][wid1]["status"] == "pending"
    assert state["_park_conditions"][wid3]["status"] == "pending"


# ── rebuild_events ──

@pytest.mark.anyio
async def test_rebuild_events_allows_complete_after_rebuild(state, coordinator):
    """Simulate resume: state loaded from disk, Events rebuilt."""
    wid = await coordinator.register(state, "hitl", {})
    # Simulate state round-trip (events lost)
    coordinator2 = ParkCoordinator()
    state2 = {"session_id": "test", "messages": [],
              "_park_conditions": state["_park_conditions"]}

    coordinator2.rebuild_events(state2)

    # complete should work because Event was rebuilt
    ok = await coordinator2.complete(state2, wid, {"answer": "resumed"})
    assert ok is True


@pytest.mark.anyio
async def test_rebuild_then_park_works(state, coordinator):
    wid = await coordinator.register(state, "hitl", {})
    # Simulate round-trip
    coordinator2 = ParkCoordinator()
    state2 = {"session_id": "test", "messages": [],
              "_park_conditions": state["_park_conditions"]}
    coordinator2.rebuild_events(state2)

    async def complete_later():
        await asyncio.sleep(0.03)
        await coordinator2.complete(state2, wid, {"answer": "later"})

    asyncio.create_task(complete_later())
    result = await coordinator2.park_round(state2)
    assert result == wid
