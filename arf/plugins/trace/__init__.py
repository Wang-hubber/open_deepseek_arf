"""TracePlugin — per-session JSONL trace recording.

Deep port: directly extends Plugin base class, writes JSONL traces
from PluginContext data, no PluginAdapter indirection.
"""
from __future__ import annotations
import json
import time
import logging
from pathlib import Path

from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext

logger = logging.getLogger("arf.plugins.trace")


class TracePlugin(Plugin):
    """Hook-mounted trace pathway — appends JSONL events to session trace file.

    Produces: {data_dir}/{session_id}/traces/{session_id}.jsonl
    """

    def __init__(self, name="trace", events=None, config=None):
        events = events or [
            {"hook_name": "before_round", "event_name": "session_start", "mode": "side"},
            {"hook_name": "before_round", "event_name": "round_start", "mode": "side"},
            {"hook_name": "after_round", "event_name": "round_end", "mode": "side"},
            {"hook_name": "after_round", "event_name": "session_end", "mode": "side"},
            {"hook_name": "before_model", "event_name": "turn_start", "mode": "side"},
            {"hook_name": "before_model", "event_name": "pre_action", "mode": "side"},
            {"hook_name": "after_model", "event_name": "post_action", "mode": "side"},
            {"hook_name": "after_model", "event_name": "turn_end", "mode": "side"},
            {"hook_name": "on_error", "event_name": "error", "mode": "side"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._enabled = self.config.get("enabled", True)

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if not self._enabled:
            return

        record = {
            "type": event_name,
            "round": ctx.interaction_round,
            "turn": ctx.turn,
            "timestamp": time.time(),
            "data": self._sanitize(ctx.hook_data),
            "session_id": ctx.session_id,
        }
        self._write_event(ctx, record)

    def _write_event(self, ctx: PluginContext, record: dict) -> None:
        """Append a JSONL line to the session trace file."""
        trace_dir = Path(ctx.data_dir) / ctx.session_id / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / f"{ctx.session_id}.jsonl"
        try:
            with open(trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Failed to write trace event for session %s", ctx.session_id)

    @staticmethod
    def _sanitize(obj):
        """Convert non-JSON-serializable values to strings."""
        if isinstance(obj, dict):
            return {str(k): TracePlugin._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [TracePlugin._sanitize(v) for v in obj]
        if isinstance(obj, Exception):
            return f"{type(obj).__name__}: {obj}"
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)

    def read_trace(self, session_id: str, data_dir: str = "./data") -> list[dict]:
        """Read all trace events for a session."""
        trace_file = Path(data_dir) / session_id / "traces" / f"{session_id}.jsonl"
        if not trace_file.exists():
            return []
        events: list[dict] = []
        with open(trace_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    def list_sessions(self, data_dir: str = "./data") -> list[str]:
        """Return all session IDs that have trace files."""
        sessions = []
        base = Path(data_dir)
        if base.exists():
            for d in base.iterdir():
                if d.is_dir() and (d / "traces").exists():
                    sessions.append(d.name)
        return sessions


Plugin = TracePlugin
