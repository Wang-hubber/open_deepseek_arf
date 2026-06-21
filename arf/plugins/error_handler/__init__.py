"""ErrorHandlerPlugin — recovery actions for engine errors.

Deep port: directly extends Plugin base class.
"""
from __future__ import annotations
import logging
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext

logger = logging.getLogger("arf.plugins.error_handler")


class ErrorHandlerPlugin(Plugin):
    """Catches engine errors and applies recovery strategies: abort, retry, skip."""

    def __init__(self, name="error_handler", events=None, config=None):
        events = events or [
            {"hook_name": "on_error", "event_name": "error", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._max_retries = self.config.get("max_transport_retry", 3)
        self._backoff_base = self.config.get("backoff_base", 1.0)
        self._backoff_max = self.config.get("backoff_max", 30.0)
        self._error_counts: dict[str, int] = {}

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "error":
            await self._handle_error(ctx)

    async def _handle_error(self, ctx: PluginContext) -> None:
        """Log error and decide recovery action."""
        exc = ctx.hook_data.get("exception")
        error_key = type(exc).__name__ if exc else "unknown"

        count = self._error_counts.get(error_key, 0) + 1
        self._error_counts[error_key] = count

        ctx.emit(event_type="error_handled", data={
            "error_type": error_key,
            "count": count,
            "detail": str(exc) if exc else "",
        })

        if count > self._max_retries:
            logger.error(
                "Error '%s' exceeded max retries (%d). Aborting.",
                error_key, self._max_retries,
            )
            ctx.hook_data["error_action"] = "abort"
        else:
            logger.warning(
                "Error '%s' (attempt %d/%d). Retrying.",
                error_key, count, self._max_retries,
            )
            ctx.hook_data["error_action"] = "retry"


Plugin = ErrorHandlerPlugin
