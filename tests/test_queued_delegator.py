"""Tests for QueuedTaskDelegator — slot scheduling and FIFO queue."""
import asyncio
import pytest
from arf.communication.queued_delegator import QueuedTaskDelegator


class TestDispatch:
    @pytest.fixture
    def d(self):
        return QueuedTaskDelegator(max_concurrent=2)

    @pytest.mark.asyncio
    async def test_dispatch_returns_dispatched_when_slot_available(self, d):
        called = []

        async def runner(task: dict) -> dict:
            called.append(task)
            return {"started": True}

        result = await d.dispatch("s1", {"msg": "hello"}, runner)

        assert result["ok"] is True
        assert result["dispatched"] is True
        assert "task_id" in result
        await asyncio.sleep(0)
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_dispatch_queues_when_slots_full(self, d):
        barrier = asyncio.Event()

        async def runner(task: dict) -> dict:
            await barrier.wait()
            return {"started": True}

        r1 = await d.dispatch("s1", {"n": 1}, runner)
        r2 = await d.dispatch("s1", {"n": 2}, runner)

        assert r1["dispatched"] is True
        assert r2["dispatched"] is True

        r3 = await d.dispatch("s1", {"n": 3}, runner)
        assert r3["ok"] is True
        assert r3["queued"] is True
        assert r3["position"] == 1

        barrier.set()

    @pytest.mark.asyncio
    async def test_session_isolation(self, d):
        async def runner(task: dict) -> dict:
            return {"started": True}

        await d.dispatch("s1", {"n": 1}, runner)
        await d.dispatch("s1", {"n": 2}, runner)

        r3 = await d.dispatch("s1", {"n": 3}, runner)
        assert r3["queued"] is True

        r4 = await d.dispatch("s2", {"msg": "other"}, runner)
        await asyncio.sleep(0)
        assert r4["dispatched"] is True


class TestComplete:
    @pytest.fixture
    def d(self):
        return QueuedTaskDelegator(max_concurrent=2)

    @pytest.mark.asyncio
    async def test_complete_releases_slot_and_dequeues(self, d):
        barrier = asyncio.Event()
        started = []

        async def runner(task: dict) -> dict:
            started.append(task)
            await barrier.wait()
            return {"started": True}

        r1 = await d.dispatch("s1", {"n": 1}, runner)
        r2 = await d.dispatch("s1", {"n": 2}, runner)
        r3 = await d.dispatch("s1", {"n": 3}, runner)

        assert r3["queued"] is True
        assert len(started) == 2

        await d.complete("s1", r1["task_id"], {"result": "done 1"})
        await asyncio.sleep(0)
        assert len(started) == 3

        barrier.set()

    @pytest.mark.asyncio
    async def test_complete_stores_result_in_pending(self, d):
        async def runner(task: dict) -> dict:
            return {"started": True}

        r1 = await d.dispatch("s1", {"n": 1}, runner)
        await asyncio.sleep(0)

        await d.complete("s1", r1["task_id"], {"result": "done"})

        pending = await d.get_pending("s1")
        assert len(pending) == 1
        assert pending[0]["result"] == "done"
        assert pending[0]["task_id"] == r1["task_id"]


class TestGetPending:
    @pytest.fixture
    def d(self):
        return QueuedTaskDelegator(max_concurrent=2)

    @pytest.mark.asyncio
    async def test_get_pending_clears_after_read(self, d):
        async def runner(task: dict) -> dict:
            return {"started": True}

        r1 = await d.dispatch("s1", {"n": 1}, runner)
        await asyncio.sleep(0)
        await d.complete("s1", r1["task_id"], {"result": "a"})

        first = await d.get_pending("s1")
        assert len(first) == 1

        second = await d.get_pending("s1")
        assert len(second) == 0

    @pytest.mark.asyncio
    async def test_get_pending_unknown_session_returns_empty(self, d):
        assert await d.get_pending("no_such_session") == []


class TestQueueStatus:
    @pytest.fixture
    def d(self):
        return QueuedTaskDelegator(max_concurrent=2)

    @pytest.mark.asyncio
    async def test_queue_status_empty_session(self, d):
        status = await d.queue_status("s1")
        assert status == {"running": [], "queued": [], "max_concurrent": 2}

    @pytest.mark.asyncio
    async def test_queue_status_shows_running_and_queued(self, d):
        barrier = asyncio.Event()

        async def runner(task: dict) -> dict:
            await barrier.wait()
            return {"started": True}

        await d.dispatch("s1", {"n": 1}, runner)
        await d.dispatch("s1", {"n": 2}, runner)
        await d.dispatch("s1", {"n": 3}, runner)

        status = await d.queue_status("s1")
        assert len(status["running"]) == 2
        assert len(status["queued"]) == 1
        assert status["queued"][0]["position"] == 1

        barrier.set()


class TestCancel:
    @pytest.fixture
    def d(self):
        return QueuedTaskDelegator(max_concurrent=1)

    @pytest.mark.asyncio
    async def test_cancel_removes_queued_task(self, d):
        barrier = asyncio.Event()

        async def runner(task: dict) -> dict:
            await barrier.wait()
            return {"started": True}

        await d.dispatch("s1", {"n": 1}, runner)
        r2 = await d.dispatch("s1", {"n": 2}, runner)

        assert r2["queued"] is True
        removed = await d.cancel("s1", r2["task_id"])
        assert removed is True

        status = await d.queue_status("s1")
        assert len(status["queued"]) == 0

        barrier.set()

    @pytest.mark.asyncio
    async def test_cancel_unknown_task_returns_false(self, d):
        assert await d.cancel("s1", "nonexistent") is False

    @pytest.mark.asyncio
    async def test_cancel_does_not_affect_running(self, d):
        async def runner(task: dict) -> dict:
            return {"started": True}

        r1 = await d.dispatch("s1", {"n": 1}, runner)
        await asyncio.sleep(0)

        removed = await d.cancel("s1", r1["task_id"])
        assert removed is False


class TestRunnerException:
    @pytest.fixture
    def d(self):
        return QueuedTaskDelegator(max_concurrent=2)

    @pytest.mark.asyncio
    async def test_runner_exception_auto_releases_slot(self, d):
        barrier = asyncio.Event()

        async def bad_runner(task: dict) -> dict:
            raise RuntimeError("runner failed")

        async def good_runner(task: dict) -> dict:
            await barrier.wait()
            return {"ok": True}

        r1 = await d.dispatch("s1", {"n": 1}, bad_runner)
        await d.dispatch("s1", {"n": 2}, good_runner)
        r3 = await d.dispatch("s1", {"n": 3}, good_runner)

        # bad_runner fails -> wrapper calls complete with error -> slot freed -> r3 dequeued
        await asyncio.sleep(0.1)

        pending = await d.get_pending("s1")
        assert any(p["task_id"] == r1["task_id"] and "error" in p for p in pending)

        barrier.set()
