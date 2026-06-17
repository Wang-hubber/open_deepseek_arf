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
from pathlib import Path

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
        self._max_task_timeout = cfg.max_task_timeout

        # Populate module-level registry — BOTH hooks and tool functions
        # read from _registry, ensuring they always share the same delegator.
        # Only one A2APlugin instance per process (singleton pattern).
        # DO NOT overwrite if already set — sub-agents also load this plugin,
        # and overwriting would orphan tasks dispatched by the parent agent.
        if _registry.delegator is None:
            _registry.delegator = QueuedTaskDelegator(max_concurrent=cfg.max_concurrent_tasks)
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
        """Inject completed results. Detect file conflicts across tasks."""
        if ctx.current_step != "call_model":
            return

        parent_sid = ctx.session_id
        pending = await _registry.delegator.get_pending(parent_sid)
        if not pending:
            return

        applied_paths: set[str] = set()

        for task_result in pending:
            task_id = task_result.get("task_id", "")
            result = {k: v for k, v in task_result.items() if k != "task_id"}
            changes = result.get("file_changes")
            changed = (
                set(changes["added"] + changes["modified"] + changes["deleted"])
                if changes else set()
            )

            conflicts = applied_paths & changed if changed else set()
            if conflicts:
                self._hold_changes(ctx, parent_sid, task_id, result, conflicts)
                content = self._format_conflict_warning(task_id, result, conflicts)
            else:
                if changed:
                    applied_paths |= changed
                content = self._format_result(task_id, result)

            ctx.state.setdefault("messages", []).append({
                "role": "tool",
                "tool_call_id": task_id,
                "content": content,
            })
            logger.info(
                "Injected A2A result for %s into parent session %s",
                task_id, parent_sid,
            )

    # ==================================================================
    # round_end -- complete slot + emit event
    # ==================================================================

    async def _on_round_end(self, ctx: PluginContext) -> None:
        """Child agent round_end: handle HITL or normal completion."""
        child_sid = ctx.session_id
        parent_sid, task_id = self._parse_child_session(child_sid)
        if parent_sid is None:
            return

        # Check for pending human decision (HITL -- don't complete)
        pending_decision = ctx.state.get("_pending_human_decision")
        if pending_decision:
            self._emit_human_decision_required(
                ctx, parent_sid, child_sid, task_id, pending_decision
            )
            return

        # Normal completion
        result = self._collect_result(ctx.state)
        # Compute file changes for conflict detection
        ws_dir = ctx.workspace_dir or "."
        from arf.plugins.a2a.tools.delegate_task.function import _snapshot_workspace
        old_snapshot = ctx.state.get("_workspace_snapshot")
        if old_snapshot is not None:
            current = _snapshot_workspace(ws_dir)
            added = [p for p in current if p not in old_snapshot]
            modified = [p for p in current if p in old_snapshot and current[p] != old_snapshot[p]]
            deleted = [p for p in old_snapshot if p not in current]
            if added or modified or deleted:
                result["file_changes"] = {
                    "added": sorted(added),
                    "modified": sorted(modified),
                    "deleted": sorted(deleted),
                }
        await _registry.delegator.complete(parent_sid, task_id, result)
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

        status = await _registry.delegator.queue_status(parent_sid)
        still_running = any(e["task_id"] == task_id for e in status["running"])
        if still_running:
            await _registry.delegator.complete(
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
        is_gate = state.get("_aborted") or ("gate_exceeded" in (state.get("_error") or ""))
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

    def _hold_changes(
        self, ctx: PluginContext, parent_sid: str,
        task_id: str, result: dict, conflict_paths: set,
    ) -> None:
        """Store conflicting task changes to disk."""
        import json
        import shutil
        import time

        ws = Path(ctx.workspace_dir or ".")
        data_dir = ctx.data_dir or "./data"
        conflict_dir = Path(data_dir) / parent_sid / "conflicts" / task_id
        conflict_dir.mkdir(parents=True, exist_ok=True)

        # Copy conflicting files
        files_dir = conflict_dir / "files"
        for path in conflict_paths:
            src = ws / path
            if src.exists():
                dst = files_dir / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))

        # Write manifest
        file_changes = result.get("file_changes", {})
        manifest = {
            "task_id": task_id,
            "conflict_paths": sorted(conflict_paths),
            "file_changes": {k: sorted(v) for k, v in file_changes.items()},
            "held_at": time.time(),
        }
        (conflict_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _format_conflict_warning(task_id: str, result: dict, conflicts: set) -> str:
        paths = ", ".join(sorted(conflicts))
        content = result.get("content", "(no output)")
        return (
            f"[A2A] Task {task_id} completed. CONFLICT: changes to {paths} "
            f"are HELD -- already modified by another task.\n"
            f"Use resolve_conflict('{task_id}') to apply or "
            f"cancel_held('{task_id}') to discard.\n\n"
            f"Result:\n{content}"
        )

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

    def _emit_human_decision_required(
        self, ctx: PluginContext,
        parent_sid: str, child_sid: str, task_id: str,
        decision: dict,
    ) -> None:
        """Emit human_decision_required event. Child session stays active."""
        from arf.core.events import AgentEvent

        partial = self._collect_result(ctx.state)

        event = AgentEvent(
            type="human_decision_required",
            data={
                "parent_session_id": parent_sid,
                "child_session_id": child_sid,
                "task_id": task_id,
                "agent_name": ctx.state.get("agent_name", ""),
                "question": decision.get("question", ""),
                "options": decision.get("options", []),
                "partial_result": partial,
            },
            session_id=child_sid,
        )
        if ctx.event_bus:
            ctx.event_bus.emit(event)
        logger.info(
            "human_decision_required: child=%s task_id=%s question=%s",
            child_sid, task_id, decision.get("question", "")[:80],
        )
