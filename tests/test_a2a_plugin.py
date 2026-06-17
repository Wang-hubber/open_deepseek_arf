"""Tests for A2A Plugin — task delegation, slot scheduling, and hook lifecycle."""
import asyncio
from unittest.mock import MagicMock

import pytest

from arf.communication.queued_delegator import QueuedTaskDelegator
from arf.core.events import AgentEvent
from arf.core.plugin_context import PluginContext
from arf.plugins.a2a.tools import _registry as a2a_registry


class _StubEngine:
    """Minimal engine stub that immediately completes astream."""

    async def astream(self, state, stop_on_text=False):
        if False:
            yield


class TestDelegateTask:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        """Reset registry before each test."""
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_delegate_dispatches_when_slot_available(self):
        """dispatch returns {dispatched: true} when under max_concurrent."""
        from arf.plugins.a2a.tools.delegate_task.function import execute

        result = await execute(agent="", task="test task", _engine=_StubEngine())

        assert result["ok"] is True
        assert result["dispatched"] is True
        assert "task_id" in result

    @pytest.mark.anyio
    async def test_delegate_queues_when_slots_full(self):
        """dispatch returns {queued: true} when slots are all occupied."""
        from arf.plugins.a2a.tools.delegate_task.function import execute  # noqa: F811

        barrier = asyncio.Event()

        async def hold_runner(task):
            await barrier.wait()
            return {"ok": True}

        # Fill both slots with held runners (inject runner directly)
        delegator = a2a_registry.delegator
        await delegator.dispatch("s1", {"n": 1}, hold_runner)
        await delegator.dispatch("s1", {"n": 2}, hold_runner)

        # Third call should queue
        r3 = await delegator.dispatch("s1", {"n": 3}, hold_runner)
        assert r3["queued"] is True
        assert r3["position"] == 1

        barrier.set()


class TestQueueStatus:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_queue_status_returns_state(self):
        from arf.plugins.a2a.tools.queue_status.function import execute

        barrier = asyncio.Event()
        async def runner(task):
            await barrier.wait()
            return {"ok": True}

        delegator = a2a_registry.delegator
        await delegator.dispatch("s1", {"n": 1}, runner)
        await delegator.dispatch("s1", {"n": 2}, runner)
        await delegator.dispatch("s1", {"n": 3}, runner)

        result = await execute(session_id="s1")

        assert result["ok"] is True
        assert len(result["running"]) == 2
        assert len(result["queued"]) == 1
        assert result["queued"][0]["position"] == 1

        barrier.set()


class TestAwaitTask:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_await_task_returns_result_when_complete(self):
        from arf.plugins.a2a.tools.await_task.function import execute

        delegator = a2a_registry.delegator
        async def runner(task):
            return {"result": "done"}

        r = await delegator.dispatch("s1", {"n": 1}, runner)
        await asyncio.sleep(0)  # let runner complete

        # Complete the task so get_pending sees it
        await delegator.complete("s1", r["task_id"], {"result": "done"})

        result = await execute(task_id=r["task_id"], session_id="s1", timeout=5)
        assert result["ok"] is True
        assert result["result"]["result"] == "done"

    @pytest.mark.anyio
    async def test_await_task_timeout_returns_error(self):
        from arf.plugins.a2a.tools.await_task.function import execute

        result = await execute(task_id="nonexistent", session_id="s1", timeout=0.1)
        assert result["ok"] is False
        assert "timeout" in result.get("error", "")


class TestCancelTask:
    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=1)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_cancel_removes_queued_task(self):
        from arf.plugins.a2a.tools.cancel_task.function import execute

        barrier = asyncio.Event()
        async def runner(task):
            await barrier.wait()
            return {"ok": True}

        delegator = a2a_registry.delegator
        await delegator.dispatch("s1", {"n": 1}, runner)  # fills slot
        r2 = await delegator.dispatch("s1", {"n": 2}, runner)  # queued

        result = await execute(task_id=r2["task_id"], session_id="s1")
        assert result["ok"] is True
        assert result["cancelled"] is True

        barrier.set()

    @pytest.mark.anyio
    async def test_cancel_running_task_returns_false(self):
        from arf.plugins.a2a.tools.cancel_task.function import execute

        async def runner(task):
            return {"ok": True}

        delegator = a2a_registry.delegator
        r1 = await delegator.dispatch("s1", {"n": 1}, runner)
        await asyncio.sleep(0)

        result = await execute(task_id=r1["task_id"], session_id="s1")
        assert result["ok"] is True
        assert result["cancelled"] is False


class TestA2APluginHooks:
    """Hook lifecycle tests: pre_action, round_end, session_end."""

    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_round_end_completes_and_emits_event(self):
        """round_end on child session: complete() + emit task_completed."""
        from arf.plugins.a2a.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_s1"
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "test"}, runner)
        await asyncio.sleep(0)
        task_id = r["task_id"]
        child_sid = f"{parent_sid}--{task_id}"

        child_ctx = PluginContext(
            session_id=child_sid,
            state={
                "session_id": child_sid,
                "messages": [
                    {"role": "user", "content": "analyze file"},
                    {"role": "assistant", "content": "Analysis complete: found 3 issues"},
                ],
                "current_turn": 2,
            },
            event_bus=MagicMock(),
        )

        await plugin.on_hook("round_end", child_ctx)

        child_ctx.event_bus.emit.assert_called_once()
        call_args = child_ctx.event_bus.emit.call_args[0][0]
        assert call_args.type == "task_completed"
        assert call_args.data["parent_session_id"] == parent_sid
        assert call_args.data["task_id"] == task_id

    @pytest.mark.anyio
    async def test_round_end_ignores_non_child_session(self):
        """round_end on a normal session (no -- pattern) is a no-op."""
        from arf.plugins.a2a.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        event_bus = MagicMock()
        ctx = PluginContext(
            session_id="normal_session",
            state={"session_id": "normal_session", "messages": []},
            event_bus=event_bus,
        )

        await plugin.on_hook("round_end", ctx)
        event_bus.emit.assert_not_called()

    @pytest.mark.anyio
    async def test_pre_action_injects_pending_results(self):
        """pre_action on parent: inject completed task results into messages."""
        from arf.plugins.a2a.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_s3"
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "x"}, runner)
        await asyncio.sleep(0)
        await delegator.complete(parent_sid, r["task_id"], {
            "content": "Task result: 5 items found",
            "turn_count": 3,
            "gate_exceeded": False,
        })

        parent_ctx = PluginContext(
            session_id=parent_sid,
            current_step="call_model",
            state={
                "session_id": parent_sid,
                "messages": [{"role": "user", "content": "do the thing"}],
            },
        )

        await plugin.on_hook("pre_action", parent_ctx)

        msgs = parent_ctx.state["messages"]
        injected = [m for m in msgs if m.get("role") == "tool"
                    and isinstance(m.get("content"), str)
                    and m["content"].startswith("[A2A]")]
        assert len(injected) == 1
        assert "5 items found" in injected[0]["content"]
        assert r["task_id"] in injected[0]["tool_call_id"]

    @pytest.mark.anyio
    async def test_pre_action_skips_on_execute_tools(self):
        """pre_action only injects during call_model, not execute_tools."""
        from arf.plugins.a2a.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_s4"
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "x"}, runner)
        await asyncio.sleep(0)
        await delegator.complete(parent_sid, r["task_id"],
                                 {"content": "done", "turn_count": 1, "gate_exceeded": False})

        ctx = PluginContext(
            session_id=parent_sid,
            current_step="execute_tools",
            state={"session_id": parent_sid, "messages": []},
        )

        await plugin.on_hook("pre_action", ctx)
        assert len(ctx.state["messages"]) == 0

    @pytest.mark.anyio
    async def test_session_end_force_completes_aborted_tasks(self):
        """session_end on child: force-complete if still running."""
        from arf.plugins.a2a.plugin import A2APlugin

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "parent_s5"
        barrier = asyncio.Event()
        async def runner(task):
            await barrier.wait()
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "x"}, runner)
        await asyncio.sleep(0)
        task_id = r["task_id"]

        child_ctx = PluginContext(
            session_id=f"{parent_sid}--{task_id}",
            state={"session_id": f"{parent_sid}--{task_id}", "messages": []},
        )

        await plugin.on_hook("session_end", child_ctx)

        pending = await delegator.get_pending(parent_sid)
        aborted = [p for p in pending if p.get("task_id") == task_id]
        assert len(aborted) == 1
        assert aborted[0].get("error") == "child_session_aborted"

        barrier.set()


class TestA2AIntegration:
    """End-to-end: delegate_task -> sub-agent runs -> round_end -> result injected."""

    @pytest.fixture(autouse=True)
    def setup_registry(self):
        a2a_registry.delegator = QueuedTaskDelegator(max_concurrent=2)
        a2a_registry.max_task_timeout = 600.0
        yield
        a2a_registry.delegator = None

    @pytest.mark.anyio
    async def test_plugin_name_and_hooks(self):
        """A2APlugin exposes correct name and hook subscriptions."""
        from arf.plugins.a2a.plugin import A2APlugin
        plugin = A2APlugin({"max_concurrent_tasks": 2})
        assert plugin.name == "a2a"
        assert "pre_action" in plugin.hooks
        assert plugin.hooks["pre_action"] == "blocking"
        assert "round_end" in plugin.hooks
        assert plugin.hooks["round_end"] == "blocking"
        assert "session_end" in plugin.hooks
        assert plugin.hooks["session_end"] == "side"

    @pytest.mark.anyio
    async def test_delegate_task_dispatches_with_engine(self):
        """delegate_task with a mock engine dispatches correctly."""
        from arf.plugins.a2a.tools.delegate_task.function import execute

        class _StubEngine:
            async def astream(self, state, stop_on_text=False):
                yield

        result = await execute(
            task="test task", agent="", session_id="int_s1",
            _engine=_StubEngine(),
        )
        assert result["ok"] is True
        assert result["dispatched"] is True
        assert "task_id" in result

    @pytest.mark.anyio
    async def test_full_pre_action_roundtrip(self):
        """pre_action hook injects results that round_end completed."""
        from arf.plugins.a2a.plugin import A2APlugin
        from unittest.mock import MagicMock  # noqa: F811

        plugin = A2APlugin({"max_concurrent_tasks": 2, "max_task_timeout": 600})
        delegator = a2a_registry.delegator

        parent_sid = "int_parent"

        # 1. Dispatch a task (simulating delegate_task tool)
        async def runner(task):
            return {"ok": True}
        r = await delegator.dispatch(parent_sid, {"task": "analyze"}, runner)
        await asyncio.sleep(0)  # runner completes
        task_id = r["task_id"]

        # 2. Simulate child agent's round_end hook
        child_ctx = PluginContext(
            session_id=f"{parent_sid}--{task_id}",
            state={
                "session_id": f"{parent_sid}--{task_id}",
                "messages": [
                    {"role": "user", "content": "analyze"},
                    {"role": "assistant", "content": "Found 42 issues in the codebase."},
                ],
                "current_turn": 3,
            },
            event_bus=MagicMock(),
        )
        await plugin.on_hook("round_end", child_ctx)

        # 3. Verify round_end emitted task_completed
        child_ctx.event_bus.emit.assert_called_once()
        event = child_ctx.event_bus.emit.call_args[0][0]
        assert event.type == "task_completed"
        assert event.data["task_id"] == task_id
        assert event.data["result"]["content"] == "Found 42 issues in the codebase."
        assert event.data["result"]["turn_count"] == 3

        # 4. Simulate parent agent's pre_action (next turn)
        parent_ctx = PluginContext(
            session_id=parent_sid,
            current_step="call_model",
            state={
                "session_id": parent_sid,
                "messages": [{"role": "user", "content": "do things"}],
            },
        )
        await plugin.on_hook("pre_action", parent_ctx)

        # 5. Result should be in parent messages
        msgs = parent_ctx.state["messages"]
        injected = [m for m in msgs if m.get("role") == "tool"
                    and "[A2A]" in m.get("content", "")]
        assert len(injected) == 1
        assert "42 issues" in injected[0]["content"]
        assert task_id in injected[0]["tool_call_id"]

        # 6. get_pending should be empty (consumed by pre_action)
        pending = await delegator.get_pending(parent_sid)
        assert len(pending) == 0

