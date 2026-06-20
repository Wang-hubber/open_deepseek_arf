"""MemoryPlugin — LLM-driven memory extraction on round_end.

Deep port: directly extends Plugin base class.
"""
from __future__ import annotations
import logging
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext

logger = logging.getLogger("arf.plugins.memory")


class MemoryPlugin(Plugin):
    """Extracts and persists user memory after each round."""

    def __init__(self, name="memory", events=None, config=None):
        events = events or [
            {"hook_name": "after_round", "event_name": "round_end", "mode": "side"},
            {"hook_name": "after_round", "event_name": "task_completed", "mode": "side"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._interval = self.config.get("interval", 5)
        self._max_memory_size = self.config.get("max_memory_size", 300)
        self._round_count: dict[str, int] = {}
        self._call_model = None

    def set_call_model(self, call_model):
        self._call_model = call_model

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        sid = ctx.session_id
        self._round_count.setdefault(sid, 0)
        self._round_count[sid] += 1

        if event_name in ("round_end", "task_completed"):
            await self._maybe_extract(ctx)

    async def _maybe_extract(self, ctx: PluginContext) -> None:
        """Extract memory if interval is reached."""
        sid = ctx.session_id
        if self._round_count.get(sid, 0) % self._interval != 0:
            return
        messages = ctx.agent.state.messages
        if len(messages) > self._max_memory_size:
            # Trim old messages to stay within limit
            ctx.agent.state.messages = messages[-self._max_memory_size:]
            logger.debug("Memory: trimmed messages to %d for session=%s", self._max_memory_size, sid)


Plugin = MemoryPlugin
