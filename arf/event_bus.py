"""InMemoryEventBus — asyncio.Queue-based event broadcasting."""
import asyncio
from arf.core.events import AgentEvent


class InMemoryEventBus:
    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []
        self._events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self._events.append(event)
        for q in self._queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, event_types: list[str] | None = None):
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues.append(q)
        try:
            while True:
                event = await q.get()
                if event_types is None or event.type in event_types:
                    yield event
        finally:
            self._queues.remove(q)

    def collected(self, event_type: str | None = None) -> list[AgentEvent]:
        if event_type:
            return [e for e in self._events if e.type == event_type]
        return list(self._events)

    def reset(self) -> None:
        self._events.clear()
        self._queues.clear()
