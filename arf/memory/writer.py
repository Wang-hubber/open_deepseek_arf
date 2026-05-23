"""RuleBasedMemoryWriter — extract facts from conversation turns using heuristics."""
from __future__ import annotations

import time
import uuid

from arf.core.protocols import MemoryEntry, MemoryStore, MemoryWriter


class RuleBasedMemoryWriter:
    """Simple heuristic-based memory writer.

    Scans assistant messages for keywords such as *prefer*, *always*,
    *never*, and *must* and stores the surrounding content as a
    *preference*-category memory entry.

    If a *model_call* callback is provided it can be used (by subclasses
    or future implementations) for LLM-guided extraction.
    """

    def __init__(self, model_call: callable | None = None) -> None:
        self._call_model = model_call

    async def extract_and_write(
        self,
        store: MemoryStore,
        turn_messages: list[dict],
        existing_entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        new_entries: list[MemoryEntry] = []
        for msg in turn_messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            if any(kw in content.lower() for kw in ["prefer", "always", "never", "must"]):
                entry = MemoryEntry(
                    id=str(uuid.uuid4()),
                    content=content[:500],
                    category="preference",
                    timestamp=time.time(),
                    source_turn=0,
                )
                await store.save(entry)
                new_entries.append(entry)
        return new_entries + existing_entries
