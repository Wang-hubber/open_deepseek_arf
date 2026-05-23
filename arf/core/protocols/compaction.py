"""Protocols for context compaction domain."""
from typing import Protocol
from arf.core.state import AgentState


class CompactionStrategy(Protocol):
    def should_compact(self, state: AgentState, threshold: float = 0.75) -> bool: ...
    async def compact(self, state: AgentState) -> AgentState: ...
