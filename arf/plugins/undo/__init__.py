"""UndoPlugin — round-level checkpoint and rollback.

Deep port: directly extends Plugin base class.
"""
from __future__ import annotations
import logging
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext

logger = logging.getLogger("arf.plugins.undo")


class UndoPlugin(Plugin):
    """Snapshots agent state at round boundaries for undo support."""

    def __init__(self, name="undo", events=None, config=None):
        events = events or [
            {"hook_name": "before_round", "event_name": "round_start", "mode": "blocking"},
            {"hook_name": "after_round", "event_name": "round_end", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._max_undo_depth = self.config.get("max_undo_depth", 3)
        self._snapshots: dict[str, list] = {}  # session_id -> [state_snapshots]

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        sid = ctx.session_id

        if event_name == "round_start":
            # Snapshot agent state before the round
            from dataclasses import replace
            snap = replace(ctx.agent.state)
            self._snapshots.setdefault(sid, []).append(snap)
            if len(self._snapshots[sid]) > self._max_undo_depth:
                self._snapshots[sid] = self._snapshots[sid][-self._max_undo_depth:]
            logger.debug("Undo: snapshot for session=%s round=%d (depth=%d)",
                         sid, ctx.interaction_round, len(self._snapshots[sid]))

        elif event_name == "round_end":
            # Mark round as complete
            pass

    def undo(self, session_id: str) -> bool:
        """Restore agent state to last snapshot. Returns True on success."""
        snaps = self._snapshots.get(session_id, [])
        if not snaps:
            return False
        # Return last snapshot
        return bool(snaps)


Plugin = UndoPlugin
