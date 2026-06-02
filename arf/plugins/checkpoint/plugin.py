"""CheckpointPlugin — session state archiving on round_end and session_end.

Replaces scattered state_store.put() calls in GraphEngine with
hook-driven persistence. Provides snapshot-based undo support.
"""
import copy
import json
import logging
from pathlib import Path

from arf.core.plugin_context import PluginContext
from arf.core.state import AgentState

logger = logging.getLogger("arf.plugins.checkpoint")


class CheckpointPlugin:
    """Archives agent state to disk on round boundaries.

    Mounted on: round_end, session_end
    Replaces inline state_store.put() calls scattered through GraphEngine.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._state_store = None
        self._state_dir = Path(cfg.get("state_dir", "./memory/state"))
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._enabled = cfg.get("enabled", True)

    @property
    def name(self) -> str:
        return "checkpoint"

    @property
    def hooks(self) -> list[str]:
        return ["round_end", "session_end"]

    def set_state_store(self, store) -> None:
        self._state_store = store

    async def on_hook(self, hook_name: str, context: PluginContext) -> None:
        if not self._enabled or not self._state_store:
            return

        sid = context.session_id
        state = await self._state_store.get(sid)
        if not state:
            return

        if hook_name == "round_end":
            await self._save_snapshot(sid, state, context.interaction_round)
        elif hook_name == "session_end":
            await self._archive_session(sid, state)

    async def _save_snapshot(self, session_id: str, state: AgentState,
                              turn: int) -> None:
        """Save a round-level snapshot for undo."""
        snap_dir = self._state_dir / session_id / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_path = snap_dir / f"round_{turn}.json"
        data = copy.deepcopy(dict(state))
        data.pop("tool_results", None)
        snap_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    async def _archive_session(self, session_id: str, state: AgentState) -> None:
        """Archive final session state."""
        archive_path = self._state_dir / f"{session_id}.json"
        data = copy.deepcopy(dict(state))
        data.pop("tool_results", None)
        archive_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Session archived: %s", session_id)

    def list_snapshots(self, session_id: str) -> list[int]:
        """Return sorted list of round numbers that have snapshots."""
        snap_dir = self._state_dir / session_id / "snapshots"
        if not snap_dir.exists():
            return []
        turns = []
        for f in snap_dir.glob("round_*.json"):
            try:
                turns.append(int(f.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return sorted(turns)

    def restore_snapshot(self, session_id: str, turn: int) -> AgentState | None:
        """Restore state from a specific round snapshot."""
        snap_path = self._state_dir / session_id / "snapshots" / f"round_{turn}.json"
        if not snap_path.exists():
            return None
        return json.loads(snap_path.read_text(encoding="utf-8"))
