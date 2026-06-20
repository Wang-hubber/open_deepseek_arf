"""MemoryPlugin — LLM-driven memory extraction and merging."""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
import arf.plugins.memory.plugin as _mod


class MemoryPlugin(_NewPlugin):
    """New-style memory plugin wrapping existing logic."""

    def __init__(self, name="memory", events=None, config=None):
        events = events or [
            {"hook_name": "after_round", "event_name": "round_end", "mode": "side"},
            {"hook_name": "after_round", "event_name": "task_completed", "mode": "side"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._old = _mod.MemoryPlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        await self._adapter.handle(event_name, ctx)


Plugin = MemoryPlugin
