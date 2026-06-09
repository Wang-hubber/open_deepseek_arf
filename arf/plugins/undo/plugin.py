"""UndoPlugin — mount points for undo/rollback operations.

CheckpointPlugin handles round-level snapshots. The undo tool function
handles the actual undo rollback. This plugin registers the lifecycle
hooks declared in plugin.yaml so the hooks system knows about them.
"""

from arf.core.plugin_context import PluginContext


class UndoPlugin:
    def __init__(self, config: dict | None = None):
        pass

    @property
    def name(self) -> str:
        return "undo"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_end": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        pass  # checkpoint snapshots handled by CheckpointPlugin
