"""Protocols for unified event system."""
from typing import Protocol, AsyncIterator
from arf.core.events import AgentEvent


class EventBus(Protocol):
    def emit(self, event: AgentEvent) -> None: ...
    async def subscribe(
        self, event_types: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]: ...




