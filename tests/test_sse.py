"""Unit tests for SSE listener lifecycle — verify no callback leaks."""
import asyncio

import pytest

from arf.core.events import AgentEvent
from arf.streaming.adapters.sse import SseStream


class TestSseListenerLifecycle:
    @pytest.fixture
    def stream(self):
        return SseStream()

    @pytest.mark.anyio
    async def test_listen_and_break_removes_callback(self, stream):
        """Breaking out of async for must remove the listener callback."""
        async def feed():
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
        task = asyncio.create_task(feed())

        async with stream.listen() as queue:
            async for _ in queue:
                break
        await task
        assert len(stream._listeners) == 0

    @pytest.mark.anyio
    async def test_listen_exception_removes_callback(self, stream):
        """Exception during iteration must remove the listener callback."""
        async def feed():
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
        task = asyncio.create_task(feed())

        with pytest.raises(ValueError, match="boom"):
            async with stream.listen() as queue:
                async for _ in queue:
                    await task
                    raise ValueError("boom")
        assert len(stream._listeners) == 0

    @pytest.mark.anyio
    async def test_listen_return_removes_callback(self, stream):
        """Normal return from context manager must remove the callback."""
        async def feed():
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
        task = asyncio.create_task(feed())

        async with stream.listen() as queue:
            async for _ in queue:
                pass  # receive one message, then loop ends because...
                break  # actually need break since queue.get() blocks
        await task
        assert len(stream._listeners) == 0

    @pytest.mark.anyio
    async def test_callback_registered_on_enter(self, stream):
        """Callback is ONLY registered when the context manager is entered."""
        # Before entering: _listeners is empty
        assert len(stream._listeners) == 0

        async def feed():
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
        task = asyncio.create_task(feed())

        async with stream.listen() as queue:
            assert len(stream._listeners) == 1
            async for _ in queue:
                break

        await task
        assert len(stream._listeners) == 0

    @pytest.mark.anyio
    async def test_multiple_listeners_only_own_removed(self, stream):
        """When one listener exits, only its callback is removed."""
        async def feed():
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
        task = asyncio.create_task(feed())

        async with stream.listen() as q1:
            async with stream.listen() as q2:
                assert len(stream._listeners) == 2
                # Receive one message from each
                await q1.__anext__()
                await q2.__anext__()
                await task
            # q2 context exits → one callback removed
            assert len(stream._listeners) == 1
        # q1 context exits → both removed
        assert len(stream._listeners) == 0

    @pytest.mark.anyio
    async def test_exit_then_new_listener(self, stream):
        """After one listener exits, a new one works without residual callbacks."""
        async def feed():
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={}))
        task1 = asyncio.create_task(feed())

        async with stream.listen() as q:
            async for _ in q:
                break
        await task1
        assert len(stream._listeners) == 0

        task2 = asyncio.create_task(feed())
        async with stream.listen() as q:
            async for _ in q:
                break
        await task2
        assert len(stream._listeners) == 0

    @pytest.mark.anyio
    async def test_stale_callback_does_not_receive_publish(self, stream):
        """After a listener exits, its callback must not be in _listeners."""
        async def feed():
            await asyncio.sleep(0.01)
            await stream.publish(AgentEvent(type="user_input", data={"n": 1}))

        task = asyncio.create_task(feed())
        async with stream.listen() as q:
            async for _ in q:
                break
        await task
        assert len(stream._listeners) == 0

        # New listener
        task2 = asyncio.create_task(feed())
        async with stream.listen() as q:
            assert len(stream._listeners) == 1
            async for _ in q:
                break
        await task2
        assert len(stream._listeners) == 0
