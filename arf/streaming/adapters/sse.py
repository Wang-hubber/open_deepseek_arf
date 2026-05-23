"""SseStream — Server-Sent Events transport adapter."""
import json
from arf.core.events import AgentEvent


class SseStream:
    def __init__(self) -> None:
        self._listeners: list = []

    async def publish(self, event: AgentEvent) -> None:
        payload = json.dumps({"type": event.type, "data": event.data, "timestamp": event.timestamp})
        data = f"data: {payload}\n\n"
        for cb in self._listeners:
            await cb(data)

    async def listen(self):
        import asyncio
        q: asyncio.Queue = asyncio.Queue()
        async def _cb(data):
            await q.put(data)
        self._listeners.append(_cb)
        try:
            while True:
                yield await q.get()
        finally:
            self._listeners.remove(_cb)
