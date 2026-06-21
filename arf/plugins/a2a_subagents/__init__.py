"""A2A Subagents Plugin — task delegation for agent-to-agent communication.

Deep port: directly extends Plugin base class.
"""
from __future__ import annotations
import logging
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.plugins.a2a_subagents.config import A2APluginConfig

logger = logging.getLogger("arf.plugins.a2a_subagents")

_DEFAULT_EVENTS = [
    {"hook_name": "before_round", "event_name": "session_start", "mode": "side"},
    {"hook_name": "before_model", "event_name": "pre_action", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "round_end", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "session_end", "mode": "side"},
]


class A2APlugin(Plugin):
    """Manages sub-agent task delegation and lifecycle."""

    def __init__(self, name="a2a_subagents", events=None, config=None):
        super().__init__(name=name, events=events or _DEFAULT_EVENTS, config=config or {})
        self._max_concurrent = self.config.get("max_concurrent_tasks", 3)
        self._max_timeout = self.config.get("max_task_timeout", 600)
        self._active_tasks: dict[str, dict] = {}

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "session_start":
            logger.debug("A2A subagents: session_start for %s", ctx.session_id)
        elif event_name == "pre_action":
            await self._process_delegations(ctx)
        elif event_name == "round_end":
            await self._check_pending(ctx)
        elif event_name == "session_end":
            await self._cleanup(ctx)

    async def _process_delegations(self, ctx: PluginContext) -> None:
        """Check for delegate_task calls and enqueue them."""
        pass  # Full implementation reads tool calls from hook_data

    async def _check_pending(self, ctx: PluginContext) -> None:
        """Wait for pending sub-agent tasks if any."""
        if self._active_tasks:
            # Signal harness to park if sub-agents are still running
            ctx.agent.wait(hook_name="after_round", reason="subagent_pending")

    async def _cleanup(self, ctx: PluginContext) -> None:
        self._active_tasks.pop(ctx.session_id, None)


Plugin = A2APlugin
__all__ = ["A2APlugin", "A2APluginConfig", "Plugin"]
