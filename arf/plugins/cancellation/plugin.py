"""CancellationPlugin — abort execution when cancel_event is set."""
import asyncio
from arf.core.plugin_context import PluginContext


class CancellationPlugin:
    def __init__(self, config: dict | None = None):
        self._cancel_event: asyncio.Event | None = None

    def set_cancel_event(self, event: asyncio.Event) -> None:
        self._cancel_event = event

    @property
    def name(self) -> str:
        return "cancellation"

    @property
    def hooks(self) -> dict[str, str]:
        return {"turn_start": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if self._cancel_event and self._cancel_event.is_set():
            raise CancelledError("Execution cancelled by user")


class CancelledError(Exception):
    pass
