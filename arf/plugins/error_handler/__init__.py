"""ErrorHandlerPlugin — 5-action recovery: abort, fallback, retry, skip, rollback."""
from arf.harness.plugin_base import Plugin as _NewPlugin
from arf.harness.context import PluginContext as _PluginContext
from arf.harness.adapter import PluginAdapter as _PluginAdapter
import arf.plugins.error_handler.plugin as _mod


class ErrorHandlerPlugin(_NewPlugin):
    """New-style error handler plugin wrapping existing logic."""

    def __init__(self, name="error_handler", events=None, config=None):
        events = events or [
            {"hook_name": "on_error", "event_name": "error", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._old = _mod.ErrorHandlerPlugin(self.config)
        self._adapter = _PluginAdapter(self._old)

    async def handle(self, event_name: str, ctx: _PluginContext) -> None:
        await self._adapter.handle(event_name, ctx)


Plugin = ErrorHandlerPlugin
