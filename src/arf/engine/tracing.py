"""Development tracer -- logs graph node execution to Python logging.

For production trace storage, trace events are written to the SQLite
database via insert_trace_events() in arf.server.database.
"""

import logging
import time

logger = logging.getLogger("arf.trace")


def build_trace_metadata(
    session_id: str = "",
    user_id: str = "",
    model_type: str = "",
    classification: str = "",
    workspace_dir: str = "",
) -> dict:
    """Build metadata dict for trace events."""
    return {
        "session_id": session_id,
        "user_id": user_id,
        "model_type": model_type,
        "classification": classification,
        "workspace": workspace_dir,
        "framework": "arf",
    }


class DevTracer:
    """Fallback tracer -- logs graph node execution to Python logging."""

    def __init__(self):
        self._logger = logging.getLogger("arf.trace")
        self._start_times: dict[str, float] = {}

    def node_start(self, node_name: str, turn: int, model: str = "") -> None:
        self._start_times[node_name] = time.monotonic()

    def node_end(self, node_name: str, turn: int, model: str = "", extra: dict | None = None) -> None:
        start = self._start_times.pop(node_name, None)
        duration_ms = (time.monotonic() - start) * 1000 if start else 0
        extras = f" {extra}" if extra else ""
        self._logger.info(
            "graph_node node=%s turn=%d model=%s duration_ms=%.1f%s",
            node_name, turn, model, duration_ms, extras,
        )
