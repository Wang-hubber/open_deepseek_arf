"""A2APlugin — hook-driven task delegation lifecycle.

Hooks:
  pre_action (call_model) — inject completed task results into parent messages
  round_end              — detect child agent -> complete() + emit task_completed
  session_end            — force-complete aborted child agents

Sub-agents are identified by session_id pattern: {parent_sid}--{task_id}.
The sub-agent itself is unaware of A2A -- it just runs its ReAct loop.
"""
from __future__ import annotations

import logging

from arf.communication.queued_delegator import QueuedTaskDelegator
from arf.core.plugin_context import PluginContext
from arf.plugins.a2a.config import A2APluginConfig
from arf.plugins.a2a.tools import _registry

logger = logging.getLogger("arf.plugins.a2a")


class A2APlugin:
    """Blocking hook plugin for A2A task delegation.

    Tools are auto-loaded by PluginProvider from arf/plugins/a2a/tools/.
    No session_start hook needed -- enabling the plugin is enough.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = A2APluginConfig(**(config or {}))
        self._delegator = QueuedTaskDelegator(max_concurrent=cfg.max_concurrent_tasks)
        self._max_task_timeout = cfg.max_task_timeout

        # Populate module-level registry so tool functions can access the delegator
        _registry.delegator = self._delegator
        _registry.max_task_timeout = self._max_task_timeout

    @property
    def name(self) -> str:
        return "a2a"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "pre_action": "blocking",
            "round_end": "blocking",
            "session_end": "side",
        }

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "pre_action":
            await self._on_pre_action(ctx)
        elif hook_name == "round_end":
            await self._on_round_end(ctx)
        elif hook_name == "session_end":
            await self._on_session_end(ctx)

    # ==================================================================
    # pre_action -- inject completed results into parent messages
    # ==================================================================

    async def _on_pre_action(self, ctx: PluginContext) -> None:
        """Inject completed task results into parent agent's message list.

        Only fires during call_model phase. The parent agent sees injected
        results on its next turn, whether it's mid-round or starting a new
        round after human input.
        """
        if ctx.current_step != "call_model":
            return  # only inject before model sees messages

        parent_sid = ctx.session_id
        pending = await self._delegator.get_pending(parent_sid)
        if not pending:
            return

        for task_result in pending:
            task_id = task_result.get("task_id", "")
            result = {k: v for k, v in task_result.items() if k != "task_id"}
            content = self._format_result(task_id, result)
            ctx.state.setdefault("messages", []).append({
                "role": "tool",
                "tool_call_id": task_id,
                "content": content,
            })
            logger.info(
                "Injected A2A result for %s into parent session %s (round=%s)",
                task_id, parent_sid, ctx.interaction_round,
            )

    # ==================================================================
    # round_end -- complete slot + emit event
    # ==================================================================

    async def _on_round_end(self, ctx: PluginContext) -> None:
        """Child agent round_end: complete delegation + emit task_completed.

        Does NOT inject into parent messages -- that's pre_action's job.
        """
        child_sid = ctx.session_id
        parent_sid, task_id = self._parse_child_session(child_sid)
        if parent_sid is None:
            return  # not a sub-agent session

        result = self._collect_result(ctx.state)
        await self._delegator.complete(parent_sid, task_id, result)
        self._emit_completed(ctx, parent_sid, child_sid, task_id, result)

    # ==================================================================
    # session_end -- force-complete aborted children
    # ==================================================================

    async def _on_session_end(self, ctx: PluginContext) -> None:
        """Force-complete child task if it aborted without calling round_end."""
        child_sid = ctx.session_id
        parent_sid, task_id = self._parse_child_session(child_sid)
        if parent_sid is None:
            return

        status = await self._delegator.queue_status(parent_sid)
        still_running = any(e["task_id"] == task_id for e in status["running"])
        if still_running:
            await self._delegator.complete(
                parent_sid, task_id,
                {"ok": False, "error": "child_session_aborted"},
            )

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _parse_child_session(child_sid: str) -> tuple[str | None, str | None]:
        """Parse {parent_sid}--{task_id} -> (parent_sid, task_id)."""
        if "--" not in child_sid:
            return None, None
        parts = child_sid.rsplit("--", 1)
        if len(parts) != 2:
            return None, None
        return parts[0], parts[1]

    @staticmethod
    def _collect_result(state: dict) -> dict:
        """Extract result summary from sub-agent's final state."""
        msgs = state.get("messages", [])
        content = ""
        for m in reversed(msgs):
            if m.get("role") == "assistant" and m.get("content", "").strip():
                content = m["content"].strip()
                break
        is_gate = state.get("_aborted") or ("gate_exceeded" in state.get("_error", ""))
        return {
            "content": content,
            "turn_count": state.get("current_turn", 0),
            "gate_exceeded": is_gate,
        }

    @staticmethod
    def _format_result(task_id: str, result: dict) -> str:
        """Format a completed task result as a message string."""
        content = result.get("content", "(no output)")
        turns = result.get("turn_count", "?")
        if result.get("gate_exceeded"):
            return (
                f"[A2A] Task {task_id} completed with gate exceeded ({turns} turns). "
                f"Partial result:\n{content}"
            )
        if result.get("error"):
            return f"[A2A] Task {task_id} failed: {result['error']}"
        return f"[A2A] Task {task_id} completed ({turns} turns):\n{content}"

    def _emit_completed(
        self, ctx: PluginContext,
        parent_sid: str, child_sid: str, task_id: str, result: dict,
    ) -> None:
        """Emit task_completed event to EventBus -> trace JSONL + app notification."""
        from arf.core.events import AgentEvent

        event = AgentEvent(
            type="task_completed",
            data={
                "parent_session_id": parent_sid,
                "child_session_id": child_sid,
                "task_id": task_id,
                "result": result,
            },
            session_id=child_sid,
        )
        if ctx.event_bus:
            ctx.event_bus.emit(event)
        logger.info(
            "task_completed: parent=%s child=%s task_id=%s",
            parent_sid, child_sid, task_id,
        )
