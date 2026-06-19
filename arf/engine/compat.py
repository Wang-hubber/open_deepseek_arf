"""Compatibility utilities for the primitive-driven engine transition.

Provides:
- collect_response(): gather final text from an astream (replaces chat())
- collect_events(): gather all events from an astream (for testing)
- PrimitiveHookAdapter: wraps old PluginProtocol plugins as PrimitiveHandler
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from arf.core.events import AgentEvent
from arf.core.primitives import Level, PrimitiveHandler

if TYPE_CHECKING:
    from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.engine.compat")


async def collect_response(
    astream: AsyncGenerator[AgentEvent, None],
) -> str:
    """Collect final text content from an astream. Replaces chat().

    Usage:
        text = await collect_response(agent.astream("hello"))
    """
    final_text = ""
    async for event in astream:
        if event.type == "model_call_end":
            content = event.data.get("content", "")
            if content:
                final_text = content
    return final_text


async def drain_astream(engine, state: dict) -> dict:
    """Drain astream events and return the final state from the state store.

    Replaces engine.invoke(state) — consumes all events and retrieves
    the persisted final state.

    Usage:
        final = await drain_astream(engine, state)
    """
    async for _ in engine.astream(state):
        pass
    session_id = state.get("session_id", "default")
    saved = await engine.state_store.get(session_id)
    return saved or state


async def collect_events(
    astream: AsyncGenerator[AgentEvent, None],
) -> list[AgentEvent]:
    """Collect all events from an astream. For testing/debugging.

    Usage:
        events = await collect_events(agent.astream("hello"))
    """
    events = []
    async for event in astream:
        events.append(event)
    return events


# Hook name → (PrimitiveHandler method, Level) mapping
_HOOK_TO_HANDLER: dict[str, tuple[str, Level]] = {
    "session_start":   ("on_input", Level.SESSION),
    "round_start":     ("on_input", Level.ROUND),
    "turn_start":      ("on_input", Level.TURN),
    "pre_action":      ("on_action_start", Level.TURN),
    "post_action":     ("on_action_end", Level.TURN),
    "tool_output":     ("on_output", Level.TURN),
    "turn_end":        ("on_output", Level.TURN),
    "round_end":       ("on_output", Level.ROUND),
    "session_end":     ("on_output", Level.SESSION),
    "session_park":    ("on_wait_start", Level.ROUND),
    "task_completed":  ("on_output", Level.ROUND),
    "error":           ("on_error", Level.TURN),
}


class PrimitiveHookAdapter:
    """Wraps old PluginProtocol plugins so they satisfy PrimitiveHandler.

    During migration, all existing plugins route through this adapter.
    Plugins that directly implement PrimitiveHandler bypass the adapter.
    """

    def __init__(self, plugins: list):
        self._plugins = list(plugins)
        self.name = "primitive_hook_adapter"

    def _get_hook_plugins(self, hook_name: str) -> list:
        """Return plugins that subscribe to a given hook."""
        return [p for p in self._plugins
                if hasattr(p, 'hooks') and hook_name in (p.hooks or {})]

    async def _fire_hook(self, hook_name: str, ctx: "PluginContext") -> None:
        """Fire a hook on all registered plugins."""
        for plugin in self._get_hook_plugins(hook_name):
            try:
                await plugin.on_hook(hook_name, ctx)
            except Exception:
                logger.exception(
                    "Plugin %s failed on hook %s", plugin.name, hook_name)

    async def on_input(self, level: Level, ctx: "PluginContext") -> None:
        hook_map = {
            Level.SESSION: "session_start",
            Level.ROUND: "round_start",
            Level.TURN: "turn_start",
        }
        if hook := hook_map.get(level):
            await self._fire_hook(hook, ctx)

    async def on_action_start(self, level: Level, ctx: "PluginContext") -> None:
        if level == Level.TURN:
            await self._fire_hook("pre_action", ctx)

    async def on_action_end(self, level: Level, ctx: "PluginContext") -> None:
        if level == Level.TURN:
            await self._fire_hook("post_action", ctx)

    async def on_output(self, level: Level, ctx: "PluginContext") -> None:
        hook_map = {
            Level.TURN: ["tool_output", "turn_end"],
            Level.ROUND: ["round_end", "task_completed"],
            Level.SESSION: ["session_end"],
        }
        for hook in hook_map.get(level, []):
            await self._fire_hook(hook, ctx)

    async def on_wait_start(self, level: Level, ctx: "PluginContext") -> None:
        if level == Level.ROUND:
            await self._fire_hook("session_park", ctx)

    async def on_wait_end(self, level: Level, ctx: "PluginContext") -> None:
        pass  # No legacy hook maps to this

    async def on_error(
        self, level: Level, ctx: "PluginContext", exc: Exception,
    ) -> None:
        await self._fire_hook("error", ctx)
