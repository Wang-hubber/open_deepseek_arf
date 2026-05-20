"""TraceCollector — in-memory event buffer, flushed to SQLite on session end."""

import hashlib
import uuid
from datetime import datetime, timezone


class TraceCollector:
    """In-memory buffer for trace events. Not thread-safe — use from a single thread.

    Events accumulate during a session. On session_end, flush() returns
    all events for batch INSERT into SQLite.

    Session affinity: call set_session() when the active session changes so
    lifecycle events are attributed to the correct session at emit time.
    """

    def __init__(self):
        self._buffer: list[dict] = []
        self._current_session_id: str = ""

    def set_session(self, session_id: str) -> None:
        """Update current session ID so subsequent emit() calls tag events correctly."""
        self._current_session_id = session_id

    @property
    def current_session_id(self) -> str:
        return self._current_session_id

    def emit(self, event: dict) -> None:
        """Add an event to the buffer with defaults for all standard fields.

        Assigns the current session_id at emit time so lifecycle events are
        attributed to the right session, not whichever session flushes them.
        """
        event.setdefault("event_id", uuid.uuid4().hex[:12])
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event.setdefault("session_id", self._current_session_id)
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

    def snapshot(self) -> list[dict]:
        """Return a copy of buffered events without clearing."""
        return list(self._buffer)

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
