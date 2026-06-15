"""Tests for recovery handlers registered on ControlPlane."""
import pytest
from arf.engine.control_plane import ControlPlane
from arf.engine.checkpoint import InMemoryStateStore
from arf.testing import InMemoryToolExecutor
from arf.core.plugin_context import PluginContext


def _ctx(state=None, hook_data=None):
    return PluginContext(
        session_id="test",
        state=state or {},
        current_step="test",
        hook_data=hook_data or {},
    )


class TestRecoveryNoop:
    @pytest.mark.anyio
    async def test_noop_does_nothing(self):
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor(),
        )
        state = {"x": 1}
        before = dict(state)

        await cp._recovery_handlers["noop"](state, _ctx(), {})

        assert state == before


class TestRecoveryRetryTurn:
    @pytest.mark.anyio
    async def test_retry_turn_decrements_current_turn_and_persists(self):
        store = InMemoryStateStore()
        cp = ControlPlane(
            state_store=store,
            tool_executor=InMemoryToolExecutor(),
        )
        state = {"current_turn": 5, "session_id": "s1"}
        ctx = _ctx(state)

        await cp._recovery_handlers["retry_turn"](state, ctx, {})

        assert state["current_turn"] == 4
        saved = await store.get("s1")
        assert saved["current_turn"] == 4


class TestRecoveryPersistState:
    @pytest.mark.anyio
    async def test_persist_state_saves_to_store(self):
        store = InMemoryStateStore()
        cp = ControlPlane(
            state_store=store,
            tool_executor=InMemoryToolExecutor(),
        )
        state = {"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]}

        await cp._recovery_handlers["persist_state"](state, _ctx(state), {})

        saved = await store.get("s1")
        assert saved["messages"] == [{"role": "user", "content": "hi"}]


class TestRecoveryInjectToolError:
    @pytest.mark.anyio
    async def test_inject_tool_error_appends_tool_result_to_messages(self):
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor(),
        )
        state = {"messages": [{"role": "assistant", "content": "ok"}]}
        ctx = _ctx(state, hook_data={
            "_pending_tool_calls": [
                {"id": "tc1", "name": "read_file"},
                {"id": "tc2", "name": "write_file"},
            ],
        })

        await cp._recovery_handlers["inject_tool_error"](
            state, ctx, {"error": "malformed params"}
        )

        msgs = state["messages"]
        assert len(msgs) == 3
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["tool_call_id"] == "tc1"
        assert "read_file" in msgs[1]["content"]
        assert "malformed params" in msgs[1]["content"]
        assert msgs[2]["role"] == "tool"
        assert msgs[2]["tool_call_id"] == "tc2"

    @pytest.mark.anyio
    async def test_inject_tool_error_empty_pending_noop(self):
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor(),
        )
        state = {"messages": []}
        ctx = _ctx(state, hook_data={})

        await cp._recovery_handlers["inject_tool_error"](state, ctx, {})

        assert state["messages"] == []


class TestRecoveryPostActionDrain:
    @pytest.mark.anyio
    async def test_post_action_drain_fires_side_post_action(self):
        cp = ControlPlane(
            state_store=InMemoryStateStore(),
            tool_executor=InMemoryToolExecutor(),
        )
        state = {"session_id": "s1", "_session_opened": True}
        ctx = _ctx(state)

        # post_action_drain fires side hooks for "post_action"
        await cp._recovery_handlers["post_action_drain"](state, ctx, {})
        # No exception = pass
