"""UndoPlugin — round-level checkpoint and rollback."""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
import arf.plugins.undo.plugin as _mod


class UndoPlugin(_NewPlugin):
    """New-style undo plugin wrapping existing logic."""

    def __init__(self, name="undo", events=None, config=None):
        events = events or [
            {"hook_name": "before_round", "event_name": "round_start", "mode": "blocking"},
            {"hook_name": "after_round", "event_name": "round_end", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._old = _mod.UndoPlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        await self._adapter.handle(event_name, ctx)


Plugin = UndoPlugin
