"""PluginAdapter — wrap old-style plugins for new AgentHarness checkpoints.

Temporary: remove after all plugins are ported to the new Plugin base class.
"""
from __future__ import annotations
import logging
from typing import Any
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext

logger = logging.getLogger("arf.harness.adapter")

# Map old hook event names → new harness checkpoints
HOOK_TO_CHECKPOINT: dict[str, str] = {
    "session_start": "before_round",
    "round_start":   "before_round",
    "turn_start":    "before_model",
    "pre_action":    "before_model",
    "post_action":   "after_model",
    "tool_output":   "after_tools",
    "turn_end":      "after_model",
    "round_end":     "after_round",
    "session_end":   "after_round",
    "session_park":  "after_round",
    "task_completed":"after_round",
    "error":         "on_error",
}

CHECKPOINT_TO_OLD_HOOK: dict[str, str] = {
    "before_round": "round_start",
    "before_model": "pre_action",
    "after_model":  "post_action",
    "before_tools": "pre_action",
    "after_tools":  "tool_output",
    "after_round":  "round_end",
    "on_error":     "error",
}


class PluginAdapter(Plugin):
    """Wrap an old-style plugin to work with AgentHarness."""

    def __init__(self, old_plugin: Any) -> None:
        self._old = old_plugin
        name = getattr(old_plugin, "name", old_plugin.__class__.__name__)

        # Build events list from old plugin's hooks dict
        old_hooks: dict[str, str] = getattr(old_plugin, "hooks", {})
        events = []
        for old_hook, mode in old_hooks.items():
            checkpoint = HOOK_TO_CHECKPOINT.get(old_hook)
            if checkpoint:
                events.append({
                    "hook_name": checkpoint,
                    "event_name": old_hook,
                    "mode": "blocking" if mode == "blocking" else "side",
                })

        super().__init__(name=name, events=events)

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        """Delegate to old plugin's hook handler method."""
        old = self._old

        # Populate ctx with old-style expectations
        ctx.hook_data.setdefault("state", ctx.agent.state)
        ctx.hook_data.setdefault("messages", ctx.agent.state.messages)
        ctx.hook_data.setdefault("session_id", ctx.session_id)

        # Call old plugin's hook handler
        handler = getattr(old, "on_" + event_name, None)
        if handler:
            await handler(ctx)
        else:
            # Try generic fire method
            fire = getattr(old, "fire", None)
            if fire:
                await fire(event_name, ctx)
