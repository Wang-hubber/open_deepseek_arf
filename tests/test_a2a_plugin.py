"""Tests for A2A Plugin — task delegation, slot scheduling, and hook lifecycle."""
import asyncio

import pytest

from arf.communication.queued_delegator import QueuedTaskDelegator
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
        a2a_registry.engine = _StubEngine()
        yield
        a2a_registry.delegator = None
        a2a_registry.engine = None

    @pytest.mark.anyio
    async def test_delegate_dispatches_when_slot_available(self):
        """dispatch returns {dispatched: true} when under max_concurrent."""
        from arf.plugins.a2a.tools.delegate_task.function import execute

        result = await execute(agent="", task="test task")

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
