"""TraceCollector — in-memory event buffer, flushed to SQLite on session end."""

import hashlib
import uuid
from datetime import datetime, timezone


class TraceCollector:
    """In-memory buffer for trace events. Not thread-safe — use from a single thread.

    Events accumulate during a session. On session_end, flush() returns
    all events for batch INSERT into SQLite.
    """

    def __init__(self):
        self._buffer: list[dict] = []

    def emit(self, event: dict) -> None:
        """Add an event to the buffer with defaults for all standard fields."""
        event.setdefault("event_id", uuid.uuid4().hex[:12])
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event.setdefault("username", "admin")
        event.setdefault("turn", 0)
        event.setdefault("node", None)
        event.setdefault("model", None)
        event.setdefault("tool_name", None)
        event.setdefault("duration_ms", None)
        event.setdefault("prompt_tokens", 0)
        event.setdefault("completion_tokens", 0)
        event.setdefault("total_tokens", 0)
        event.setdefault("status", "ok")
        event.setdefault("error_msg", None)
        event.setdefault("metadata", {})
        self._buffer.append(event)

    def flush(self) -> list[dict]:
        """Return all buffered events and clear the buffer."""
        events = list(self._buffer)
        self._buffer.clear()
        return events

    def __len__(self) -> int:
        return len(self._buffer)


def compute_prompt_hash(prompt_text: str) -> str:
    """Return first 16 hex chars of SHA-256 for prompt dedup."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
