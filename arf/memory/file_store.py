"""FileMemoryStore — JSON-file-backed memory persistence."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from arf.core.protocols import MemoryEntry, MemoryStore


class FileMemoryStore:
    """Memory store backed by a single JSON file on disk.

    All entries are kept in *memory.json* under *workspace*. Reads are
    O(n) and fine for hundreds of entries; no indexing is performed.
    """

    def __init__(self, workspace: str | Path = "./data/memory") -> None:
        self._dir = Path(workspace)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def save(self, entry: MemoryEntry) -> None:
        """Write *entry*, replacing an existing entry with the same id."""
        entries = await self._load_all()
        entries = [e for e in entries if e.id != entry.id]
        entries.append(entry)
        self._write(entries)

    async def load(self, session_id: str) -> list[MemoryEntry]:
        """Return every stored entry (session_id is currently unused)."""
        return await self._load_all()

    async def delete(self, entry_id: str) -> None:
        """Remove the entry matching *entry_id* (no-op if absent)."""
        entries = [e for e in await self._load_all() if e.id != entry_id]
        self._write(entries)

    async def _load_all(self) -> list[MemoryEntry]:
        path = self._dir / "memory.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [MemoryEntry(**d) for d in data]

    def _write(self, entries: list[MemoryEntry]) -> None:
        (self._dir / "memory.json").write_text(
            json.dumps(
                [
                    {
                        "id": e.id,
                        "content": e.content,
                        "category": e.category,
                        "timestamp": e.timestamp,
                        "source_turn": e.source_turn,
                        "relevance_score": e.relevance_score,
                        "replaces": e.replaces,
                    }
                    for e in entries
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
