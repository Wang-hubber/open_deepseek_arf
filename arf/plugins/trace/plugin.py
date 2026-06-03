"""TracePlugin — records all lifecycle events for observability and replay.

Mounted on: all hooks (cross-cutting).
Each event is a JSON line in <trace_dir>/<session_id>.jsonl.
"""
import json
import logging
import time
from pathlib import Path

from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.plugins.trace")


class TracePlugin:
    """Records every hook invocation to a trace file.

    Mounted on all hook points. Trace files are JSONL — one JSON object
    per line, append-only. Used for debugging, replay, and evaluation.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._trace_dir = Path(cfg.get("trace_dir", "./data/traces"))
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        self._enabled = cfg.get("enabled", True)

    @property
    def name(self) -> str:
        return "trace"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "session_start": "side",
            "session_end": "side",
            "pre_model_call": "side",
            "post_model_call": "side",
            "pre_tool_exec": "side",
            "post_tool_exec": "side",
            "post_permission": "side",
            "round_end": "side",
            "sandbox_persist": "side",
        }

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        if not self._enabled:
            return

        event = {
            "type": hook_name,
            "session_id": context.session_id,
            "turn": context.interaction_round,
            "timestamp": time.time(),
            "data": context.hook_data,
        }
        self._write_event(context.session_id, event)

    def _write_event(self, session_id: str, event: dict) -> None:
        trace_file = self._trace_dir / f"{session_id}.jsonl"
        try:
            with open(trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Failed to write trace event for session %s", session_id)

    def read_trace(self, session_id: str) -> list[dict]:
        """Read all trace events for a session."""
        trace_file = self._trace_dir / f"{session_id}.jsonl"
        if not trace_file.exists():
            return []
        events = []
        with open(trace_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events
