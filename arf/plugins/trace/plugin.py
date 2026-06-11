"""TracePlugin — unified trace pathway for observability, replay, and eval.

Mounted on all hook points (side). Subscribes to EventBus for fine-grained
engine events. Produces trajectory-level JSONL at {trace_dir}/{session_id}.jsonl.
"""
import asyncio
import json
import logging
import time
from pathlib import Path

from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.plugins.trace")

# Events to skip — high-frequency streaming events, not useful for trace
_SKIP_TYPES = {"thinking_delta"}


class TracePlugin:
    """Single trace pathway — hook callbacks + EventBus subscription.

    Produces one JSONL file per session. Each line is a self-contained
    JSON object. Append-only, O(1) per write.

    Usage:
        plugin = TracePlugin({"trace_dir": "./data/traces"})
        plugin.set_event_bus(event_bus)  # called by BaseAgent
        # on_hook() called by framework
        events = plugin.read_trace("session_123")
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._trace_dir = Path(cfg.get("trace_dir", "./data/traces"))
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        self._enabled = cfg.get("enabled", True)
        self._event_bus = None
        self._consume_task: asyncio.Task | None = None

    def set_event_bus(self, event_bus) -> None:
        """Wire EventBus for fine-grained engine event subscription.

        Called by BaseAgent after plugin discovery. Starts the background
        consume task if the plugin is enabled and an event loop is running.
        """
        self._event_bus = event_bus
        if self._enabled and self._event_bus:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return  # no running event loop — called from sync test
                        # context; will be started by BaseAgent in production
            self._consume_task = asyncio.create_task(self._consume_eventbus())

    # -- PluginProtocol --------------------------------------------------

    @property
    def name(self) -> str:
        return "trace"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "session_start": "side",
            "session_end": "side",
            "round_start": "side",
            "round_end": "side",
            "turn_start": "side",
            "turn_end": "side",
            "pre_action": "side",
            "post_action": "side",
        }

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        if not self._enabled:
            return

        event = {
            "type": hook_name,
            "turn": context.interaction_round,
            "timestamp": time.time(),
            "data": self._sanitize(dict(context.hook_data)),
            "session_id": context.session_id,
        }
        self._write_event(context.session_id, event)

    # -- EventBus subscription -------------------------------------------

    async def _consume_eventbus(self) -> None:
        """Background task: consume EventBus events, write to JSONL."""
        try:
            async for event in self._event_bus.subscribe():
                if event.type in _SKIP_TYPES:
                    continue
                if event.type in ("session_start", "session_end"):
                    continue
                record = {
                    "type": event.type,
                    "turn": event.turn,
                    "timestamp": event.timestamp,
                    "data": self._sanitize(event.data),
                    "session_id": event.session_id,
                }
                self._write_event(event.session_id, record)
        except asyncio.CancelledError:
            pass

    # -- Serialization ---------------------------------------------------

    @staticmethod
    def _sanitize(obj):
        """Convert non-JSON-serializable values to strings."""
        if isinstance(obj, dict):
            return {k: TracePlugin._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [TracePlugin._sanitize(v) for v in obj]
        if isinstance(obj, Exception):
            return f"{type(obj).__name__}: {obj}"
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)

    def _write_event(self, session_id: str, record: dict) -> None:
        trace_file = self._trace_dir / f"{session_id}.jsonl"
        try:
            with open(trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Failed to write trace event for session %s",
                             session_id)

    # -- Public read API -------------------------------------------------

    def read_trace(self, session_id: str) -> list[dict]:
        """Read all trace events for a session. Returns [] if not found."""
        trace_file = self._trace_dir / f"{session_id}.jsonl"
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

    def list_sessions(self) -> list[str]:
        """Return all session IDs that have trace files."""
        return [p.stem for p in self._trace_dir.glob("*.jsonl")]
