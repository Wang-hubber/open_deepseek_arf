"""Protocols for memory domain."""
from typing import Protocol
from dataclasses import dataclass


@dataclass
class MemoryEntry:
    id: str
    content: str
    category: str
    timestamp: float
    source_turn: int
    relevance_score: float = 1.0
    replaces: str | None = None


class MemoryStore(Protocol):
    async def save(self, entry: MemoryEntry) -> None: ...
    async def load(self, session_id: str) -> list[MemoryEntry]: ...
    async def delete(self, entry_id: str) -> None: ...


class MemoryRetriever(Protocol):
    async def retrieve(
        self,
        store: MemoryStore,
        query_context: str,
        session_id: str,
        max_tokens: int = 2000,
        top_k: int = 5,
    ) -> list[MemoryEntry]: ...


class MemoryWriter(Protocol):
    async def extract_and_write(
        self,
        store: MemoryStore,
        turn_messages: list[dict],
        existing_entries: list[MemoryEntry],
    ) -> list[MemoryEntry]: ...
