"""TracePlugin — hook-mounted trace pathway for observability, replay, and eval.

Mounted on all hook points (side). Engine events are injected into hook_data
via ctx.inject_engine_event() and flattened into JSONL rows at post_action.
Produces trajectory-level JSONL at {data_dir}/{session_id}/traces/{session_id}.jsonl.
"""
import json
import logging
import time
from pathlib import Path

from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.plugins.trace")


class TracePlugin:
    """Single trace pathway — hook callbacks write flat JSONL.

    Engine events (model_call_start/end, tool_call_start/end) are injected
    by the engine into hook_data._engine_events and flattened into individual
    JSONL rows when post_action fires. Hook boundaries (turn_start, turn_end,
    etc.) are also recorded as standalone rows.

    Produces one JSONL file per session. Each line is a self-contained
    JSON object. Append-only, O(1) per write.

    Usage:
        plugin = TracePlugin({"data_dir": "./data"})
        # on_hook() called by framework
        events = plugin.read_trace("session_123")
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._data_dir = Path(cfg.get("data_dir", "./data"))
        self._enabled = cfg.get("enabled", True)

        # Config snapshot — lazy, built on first _write_event call
        self._config_hash: str | None = None
        plugins_root = cfg.get("plugins_root", "./arf/plugins")
        extra_files = cfg.get("config_files", [])
        extra_roots = cfg.get("extra_roots", [])
        from arf.plugins.trace.snapshot import EnvSnapshotBuilder
        self._snapshot_builder = EnvSnapshotBuilder(plugins_root, extra_files, extra_roots)

    def set_data_dir(self, data_dir: str) -> None:
        """Override data directory (called by base.py with computed data_dir)."""
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def shutdown(self) -> None:
        """No-op — kept for PluginProtocol compatibility."""

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

        session_id = context.session_id
        interaction_round = context.interaction_round
        current_turn = context.turn

        # Flatten injected engine events at round_start (user_input) and
        # post_action (model_call_*, tool_call_*).
        if hook_name in ("round_start", "post_action"):
            engine_events = context.hook_data.pop("_engine_events", [])
            for ee in engine_events:
                record = {
                    "type": ee["type"],
                    "round": interaction_round,
                    "turn": current_turn,
                    "timestamp": ee.get("timestamp", time.time()),
                    "data": self._sanitize(ee.get("data", {})),
                    "session_id": session_id,
                }
                self._write_event(session_id, record)

        # Hook boundary event
        event = {
            "type": hook_name,
            "round": interaction_round,
            "turn": current_turn,
            "timestamp": time.time(),
            "data": self._sanitize(dict(context.hook_data)),
            "session_id": session_id,
        }
        self._write_event(session_id, event)

    # -- Config snapshot -------------------------------------------------

    def _ensure_snapshot(self) -> str:
        """Build and persist config snapshot on first call. Returns hash."""
        if self._config_hash is not None:
            return self._config_hash

        xml_str, hash_val = self._snapshot_builder.build()
        snapshot_dir = self._data_dir / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = snapshot_dir / f"{hash_val}.xml"
        if not snapshot_file.exists():
            snapshot_file.write_text(xml_str, encoding="utf-8")

        self._config_hash = hash_val
        return hash_val

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
        record["config_hash"] = self._ensure_snapshot()
        trace_dir = self._data_dir / session_id / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / f"{session_id}.jsonl"
        try:
            with open(trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Failed to write trace event for session %s",
                             session_id)

    # -- Public read API -------------------------------------------------

    def read_trace(self, session_id: str) -> list[dict]:
        """Read all trace events for a session. Returns [] if not found."""
        trace_file = self._data_dir / session_id / "traces" / f"{session_id}.jsonl"
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
        sessions = []
        for d in self._data_dir.iterdir():
            if d.is_dir() and (d / "traces").exists():
                sessions.append(d.name)
        return sessions
