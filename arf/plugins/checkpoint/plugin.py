"""CheckpointPlugin — round-level snapshot and undo."""
from arf.core.plugin_context import PluginContext
from arf.engine.round_manager import RoundManager


class CheckpointPlugin:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._rounds = RoundManager(max_undo_depth=cfg.get("max_undo_depth", 3))
        self._workspace_dir = cfg.get("workspace_dir", "")

    @property
    def name(self) -> str:
        return "checkpoint"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_start": "blocking", "round_end": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "round_start":
            self._rounds.begin_round(ctx.state, self._workspace_dir)
        elif hook_name == "round_end":
            self._rounds.close_round()

    def undo(self, steps: int = 1) -> dict | None:
        return self._rounds.undo(steps, self._workspace_dir)

    def count(self) -> int:
        return self._rounds.count()
