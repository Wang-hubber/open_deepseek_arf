"""ApprovalPlugin — human-in-the-loop tool approval."""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter

# Old plugin uses a module-level class in approval/__init__.py
# We need to import the actual plugin class
import importlib
import arf.plugins.approval.plugin as _mod


class ApprovalPlugin(_NewPlugin):
    """New-style approval plugin wrapping existing logic."""

    def __init__(self, name="approval", events=None, config=None):
        events = events or [
            {"hook_name": "before_tools", "event_name": "pre_action", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._old = _mod.ApprovalPlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        await self._adapter.handle(event_name, ctx)


Plugin = ApprovalPlugin
