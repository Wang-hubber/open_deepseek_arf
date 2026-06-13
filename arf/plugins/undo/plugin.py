"""UndoPlugin — round-level checkpoint and rollback via RoundManager.

Hooks:
  round_start  — begin_round(): snapshot state + workspace files
  round_end    — close_round(): mark round complete

Public API:
  undo(steps, session_id, workspace_dir) → AgentState | None
  checkpoint_count() → int
"""

from arf.core.plugin_context import PluginContext
from arf.plugins.undo.round_manager import RoundManager
from arf.core.state import AgentState


class UndoPlugin:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        max_undo = cfg.get("max_undo_depth", 3)
        self._rm = RoundManager(max_undo_depth=max_undo)

    @property
    def name(self) -> str:
        return "undo"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_start": "blocking", "round_end": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "round_start":
            self._rm.begin_round(
                ctx.state,
                workspace_dir=getattr(ctx, "workspace_dir", ""),
            )
        elif hook_name == "round_end":
            self._rm.close_round()

    def undo(self, steps: int, session_id: str = "",
             workspace_dir: str = "") -> AgentState | None:
        return self._rm.undo(steps, workspace_dir=workspace_dir)

    def checkpoint_count(self) -> int:
        return self._rm.count()

    def get_round_manager(self) -> RoundManager:
        return self._rm
