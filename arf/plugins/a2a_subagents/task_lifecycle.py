"""A2ATaskLifecycle — sub-agent completion with delegator integration."""
from __future__ import annotations

import logging

from arf.core.plugin_context import PluginContext
from arf.core.protocols.task_lifecycle import DefaultTaskLifecycle

logger = logging.getLogger("arf.plugins.a2a_subagents.task_lifecycle")


class A2ATaskLifecycle(DefaultTaskLifecycle):
    """Calls delegator.complete() in addition to emitting EventBus event."""

    def __init__(self, event_bus, delegator,
                 parent_sid: str = "", child_sid: str = "", task_id: str = "") -> None:
        super().__init__(event_bus)
        self._delegator = delegator
        self._parent_sid = parent_sid
        self._child_sid = child_sid
        self._task_id = task_id

    async def complete(
        self, result: str, files_changed: dict[str, list[str]],
        confidence: float, notes: str, ctx: PluginContext,
    ) -> dict:
        r = await super().complete(
            result, files_changed, confidence, notes, ctx)

        if self._delegator and self._parent_sid and self._task_id:
            complete_result = {
                "ok": True, "content": result,
                "turn_count": ctx.state.get("current_turn", 0),
                "gate_exceeded": False,
            }
            if files_changed:
                complete_result["file_changes"] = files_changed
            try:
                await self._delegator.complete(
                    self._parent_sid, self._task_id, complete_result)
            except Exception:
                logger.exception(
                    "Failed to complete delegator task %s", self._task_id)
        return r
