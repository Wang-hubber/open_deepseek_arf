"""TaskLifecycleProtocol — engine interface for task completion signaling."""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from arf.core.events import AgentEvent
from arf.core.plugin_context import PluginContext

logger = logging.getLogger("arf.task_lifecycle")


@runtime_checkable
class TaskLifecycleProtocol(Protocol):
    """Protocol for task completion signaling.

    Called by the engine when a tool returns task_complete=True.
    """

    async def complete(
        self, result: str, files_changed: dict[str, list[str]],
        confidence: float, notes: str, ctx: PluginContext,
    ) -> dict:
        """Signal task completion. Returns {"task_id": str, "status": "completed"}."""
        ...


class DefaultTaskLifecycle:
    """EventBus-driven task completion. Emits task_completed event.
    The task_completed hook is fired separately by the engine after round_end."""

    def __init__(self, event_bus) -> None:
        self._event_bus = event_bus

    async def complete(
        self, result: str, files_changed: dict[str, list[str]],
        confidence: float, notes: str, ctx: PluginContext,
    ) -> dict:
        task_id = f"{ctx.session_id}_{ctx.interaction_round}"
        start_round = ctx.state.get("_task_start_round", 0)
        if self._event_bus:
            self._event_bus.emit(AgentEvent(
                type="task_completed",
                data={
                    "task_id": task_id, "session_id": ctx.session_id,
                    "start_round": start_round,
                    "finish_round": ctx.interaction_round,
                    "result": result, "confidence": confidence, "notes": notes,
                },
                session_id=ctx.session_id,
            ))
        logger.info("task_completed: sid=%s task_id=%s rounds=%d-%d",
                     ctx.session_id, task_id, start_round, ctx.interaction_round)
        return {"task_id": task_id, "status": "completed"}
