"""CompactionPlugin — compact context before model calls.

Provides a new-style Plugin class for AgentHarness. Internally wraps the
existing SlidingWindowCompactor + CompactionPlugin logic via PluginAdapter
during migration.
"""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
from arf.plugins.compaction.plugin import CompactionPlugin as _OldCompactionPlugin


class CompactionPlugin(_NewPlugin):
    """New-style compaction plugin wrapping existing logic."""

    def __init__(self, name="compaction", events=None, config=None):
        events = events or [
            {"hook_name": "before_model", "event_name": "compact", "mode": "blocking"},
            {"hook_name": "after_tools", "event_name": "tool_output", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._old = _OldCompactionPlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        """Delegate to old plugin via adapter."""
        await self._adapter.handle(event_name, ctx)


# Re-export for new harness loader
Plugin = CompactionPlugin
