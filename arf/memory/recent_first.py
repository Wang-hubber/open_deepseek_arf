"""RecentFirstRetriever — return most recent N entries sorted by timestamp."""
from __future__ import annotations

from arf.core.protocols import MemoryEntry, MemoryStore, MemoryRetriever


class RecentFirstRetriever:
    """Retrieves the *top_k* most recent entries, then trims by *max_tokens*."""

    async def retrieve(
        self,
        store: MemoryStore,
        query_context: str,
        session_id: str,
        max_tokens: int = 2000,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        entries = await store.load(session_id)
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        result = entries[:top_k]
        total_chars = sum(len(e.content) for e in result)
        while total_chars > max_tokens * 3 and len(result) > 1:
            result.pop()
            total_chars = sum(len(e.content) for e in result)
        return result
