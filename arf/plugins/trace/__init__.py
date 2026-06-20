"""TracePlugin — per-session JSONL trace recording.

Provides a new-style Plugin class for AgentHarness. Internally wraps the
existing TracePlugin logic via PluginAdapter during migration.
"""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
from arf.plugins.trace.plugin import TracePlugin as _OldTracePlugin


class TracePlugin(_NewPlugin):
    """New-style trace plugin wrapping existing logic."""

    def __init__(self, name="trace", events=None, config=None):
        events = events or [
            {"hook_name": "before_round", "event_name": "session_start", "mode": "side"},
            {"hook_name": "before_round", "event_name": "round_start", "mode": "side"},
            {"hook_name": "after_round", "event_name": "round_end", "mode": "side"},
            {"hook_name": "after_round", "event_name": "session_end", "mode": "side"},
            {"hook_name": "before_model", "event_name": "turn_start", "mode": "side"},
            {"hook_name": "before_model", "event_name": "pre_action", "mode": "side"},
            {"hook_name": "after_model", "event_name": "post_action", "mode": "side"},
            {"hook_name": "after_model", "event_name": "turn_end", "mode": "side"},
            {"hook_name": "on_error", "event_name": "error", "mode": "side"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._old = _OldTracePlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        """Delegate to old plugin via adapter."""
        await self._adapter.handle(event_name, ctx)


# Re-export for new harness loader
Plugin = TracePlugin
