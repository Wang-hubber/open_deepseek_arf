"""Protocols for observability domain."""
from typing import Protocol
from arf.core.events import AgentEvent


class Tracer(Protocol):
    async def consume(self, events: list[AgentEvent]) -> None: ...
    async def flush(self) -> None: ...
