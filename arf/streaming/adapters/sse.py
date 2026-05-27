"""SseStream — Server-Sent Events transport adapter."""
import asyncio
import json
from contextlib import asynccontextmanager

from arf.core.events import AgentEvent


class SseStream:
    def __init__(self) -> None:
        self._listeners: list = []

    async def publish(self, event: AgentEvent) -> None:
        payload = json.dumps({"type": event.type, "data": event.data, "timestamp": event.timestamp})
        data = f"data: {payload}\n\n"
        for cb in self._listeners:
            await cb(data)

    @asynccontextmanager
    async def listen(self):
        """Context manager that yields an async queue of SSE messages.

        Usage::

            async with stream.listen() as queue:
                async for msg in queue:
                    yield msg

        The callback is registered on enter and removed on exit, regardless
        of how the block exits (break, return, exception, normal completion).
        """
        q: asyncio.Queue = asyncio.Queue()

        async def _cb(data):
            await q.put(data)

        self._listeners.append(_cb)
        try:
            yield _iter_queue(q)
        finally:
            try:
                self._listeners.remove(_cb)
            except ValueError:
                pass  # already removed


def _iter_queue(q: asyncio.Queue):
    """Async generator wrapping an asyncio.Queue for async for iteration."""

    async def _gen():
        while True:
            yield await q.get()

    return _gen()
