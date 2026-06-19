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
from arf.plugins.a2a_subagents.config import A2APluginConfig
from arf.plugins.a2a_subagents.tools import _registry

logger = logging.getLogger("arf.plugins.a2a_subagents")


class A2APlugin:
    """Blocking hook plugin for A2A task delegation.

    Tools are auto-loaded by PluginProvider from arf/plugins/a2a/tools/.
    No session_start hook needed -- enabling the plugin is enough.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = A2APluginConfig(**(config or {}))
        self._max_task_timeout = cfg.max_task_timeout
        self._child_resume = cfg.child_resume  # "auto" or "notify"

        # Populate module-level registry — BOTH hooks and tool functions
        # read from _registry, ensuring they always share the same delegator.
        # Only one A2APlugin instance per process (singleton pattern).
        # DO NOT overwrite if already set — sub-agents also load this plugin,
        # and overwriting would orphan tasks dispatched by the parent agent.
        if _registry.delegator is None:
            _registry.delegator = QueuedTaskDelegator(max_concurrent=cfg.max_concurrent_tasks)
        _registry.max_task_timeout = self._max_task_timeout

        # Store child_resume on registry so delegate_task can access it
        _registry.child_resume = self._child_resume

        # Cascade cancel tracking on registry — shared with delegate_task
        if not hasattr(_registry, "cancel_events"):
            _registry.cancel_events = {}

    def child_cancel_event(self, child_session_id: str) -> "asyncio.Event":
        """Create and register a cancel_event for a child sub-agent."""
        import asyncio as _asyncio
        event = _asyncio.Event()
        _registry.cancel_events[child_session_id] = event
        return event

    async def cascade_cancel(self, ctx: PluginContext) -> None:
        """Set all registered child cancel_events and update child_tasks.

        Only cancels children that belong to this parent session, to prevent
        cross-parent interference from the global cancel_events dict.
        """
        parent_sid = ctx.session_id
        from arf.engine.checkpoint import FileStateStore
        from pathlib import Path
        parent_state = await FileStateStore(Path(ctx.data_dir or "./data")).get(parent_sid)
        child_sids: set[str] = set()
        if parent_state:
            child_sids = {ct["child_session_id"] for ct in parent_state.get("child_tasks", [])}

        for child_sid, event in list(_registry.cancel_events.items()):
            if child_sid not in child_sids:
                continue  # Not this parent's child
            if not event.is_set():
                event.set()
                await self._update_child_status(ctx, parent_sid, child_sid, "cancelled")
            _registry.cancel_events.pop(child_sid, None)

    async def _update_child_status(self, ctx: PluginContext, parent_sid: str,
                                    child_session_id: str, status: str) -> None:
        """Update a child_tasks entry's status in the parent state store."""
        from arf.engine.checkpoint import FileStateStore
        try:
            parent_store = FileStateStore(Path(ctx.data_dir or "./data"))
            parent_state = await parent_store.get(parent_sid)
            if parent_state is None:
                return
            for ct in parent_state.get("child_tasks", []):
                if ct.get("child_session_id") == child_session_id:
                    ct["status"] = status
                    break
            await parent_store.put(parent_sid, parent_state)
        except Exception:
            logger.exception("Failed to update child_tasks status for %s", child_session_id)

    @property
    def name(self) -> str:
        return "a2a_subagents"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "session_start": "side",
            "pre_action": "blocking",
            "round_end": "blocking",
            "session_end": "side",
        }

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "session_start":
            await self._on_session_start(ctx)
        elif hook_name == "pre_action":
            await self._on_pre_action(ctx)
        elif hook_name == "round_end":
            await self._on_round_end(ctx)
        elif hook_name == "session_end":
            await self._on_session_end(ctx)

    # ==================================================================
    # session_start -- capture park_coordinator
    # ==================================================================

    async def _on_session_start(self, ctx: PluginContext) -> None:
        """Capture park_coordinator from hook_data into shared registry."""
        if "park_coordinator" in ctx.hook_data and _registry.park_coordinator is None:
            _registry.park_coordinator = ctx.hook_data["park_coordinator"]

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

            # Inject as user-role, not tool-role.  Sub-agent results are
            # external input — the task_id is a delegator-internal ID that
            # does NOT match any tool_calls[].id in the parent's messages.
            # Using role:"tool" with a mismatched tool_call_id violates the
            # chat API contract (same issue as peer message injection).
            ctx.state.setdefault("messages", []).append({
                "role": "user",
                "content": content,
            })
            logger.info(
                "Injected A2A result for %s into parent session %s",
                task_id, parent_sid,
            )

    # ==================================================================
    # round_end -- clean up registry state
    # ==================================================================

    async def _on_round_end(self, ctx: PluginContext) -> None:
        """Child agent round_end: clean up registry state.

        Primitive handling (HITL request / task completion) is now done
        by the engine via A2AHITL / A2ATaskLifecycle protocols injected
        into the child agent's engine.
        """
        child_sid = ctx.session_id
        parent_sid, task_id = self._parse_child_session(child_sid)
        if parent_sid is None:
            return

        # Don't mark as completed if waiting for human input
        if ctx.state.get("_pending_human_decision"):
            return

        # Update child_tasks status for normal completions
        await self._update_child_status(ctx, parent_sid, child_sid, "completed")

        # Clean up cancel_event from registry
        _registry.cancel_events.pop(child_sid, None)

    # ==================================================================
    # session_end -- force-complete aborted children
    # ==================================================================

    async def _on_session_end(self, ctx: PluginContext) -> None:
        """Handle child session end or cascade cancel from parent."""
        child_sid = ctx.session_id
        parent_sid, task_id = self._parse_child_session(child_sid)

        if parent_sid is not None:
            # This is a CHILD session ending — force-complete in delegator
            status = await _registry.delegator.queue_status(parent_sid)
            still_running = any(e["task_id"] == task_id for e in status["running"])
            if still_running:
                await _registry.delegator.complete(
                    parent_sid, task_id,
                    {"ok": False, "error": "child_session_aborted"},
                )
                await self._update_child_status(ctx, parent_sid, child_sid, "error")
            _registry.cancel_events.pop(child_sid, None)
        else:
            # This is the PARENT session ending — cascade cancel to all children
            # Only cascade if this session actually has children
            child_tasks = ctx.state.get("child_tasks", [])
            if child_tasks:
                await self.cascade_cancel(ctx)

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
        result = {
            "content": content,
            "turn_count": state.get("current_turn", 0),
            "gate_exceeded": is_gate,
        }
        tool_calls = state.get("_tool_calls_summary")
        if tool_calls:
            result["tool_calls_summary"] = tool_calls
        return result

    @staticmethod
    def _format_result(task_id: str, result: dict) -> str:
        """Format a completed task result as a message string."""
        content = result.get("content", "(no output)")
        turns = result.get("turn_count", "?")
        tc_summary = result.get("tool_calls_summary", [])
        fchanges = result.get("file_changes", {})

        if result.get("gate_exceeded"):
            main = (
                f"[A2A] Task {task_id} completed with gate exceeded ({turns} turns). "
                f"Partial result:\n{content}"
            )
        elif result.get("error"):
            main = f"[A2A] Task {task_id} failed: {result['error']}"
        else:
            main = f"[A2A] Task {task_id} completed ({turns} turns):\n{content}"

        parts = []
        if tc_summary:
            success_count = sum(1 for tc in tc_summary if tc.get("success"))
            parts.append(f"[Tool calls: {len(tc_summary)} total, {success_count} ok]")
        if fchanges:
            change_parts = []
            if fchanges.get("added"):
                change_parts.append(f"+{len(fchanges['added'])} added")
            if fchanges.get("modified"):
                change_parts.append(f"~{len(fchanges['modified'])} modified")
            if fchanges.get("deleted"):
                change_parts.append(f"-{len(fchanges['deleted'])} deleted")
            if change_parts:
                parts.append(f"[Files: {', '.join(change_parts)}]")

        if parts:
            return main + "\n" + "\n".join(parts)
        return main

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

    async def resume_child_agents(self, parent_state: dict, data_dir: str) -> dict:
        """Auto-resume all unfinished child agents. Returns updated parent_state."""
        from arf.agent.base import BaseAgent
        from arf.agent.config import AgentConfig
        from arf.agent.app_context import AppContext
        from arf.plugins.a2a_subagents.tools.delegate_task.function import _resolve_agent_config
        from arf.engine.checkpoint import FileStateStore

        child_tasks = parent_state.get("child_tasks", [])
        unfinished = [ct for ct in child_tasks if ct["status"] in ("running", "pending")]

        if not unfinished:
            return parent_state

        child_store = FileStateStore(data_dir)

        for ct in unfinished:
            child_sid = ct["child_session_id"]
            agent_name = ct["agent_name"]
            task_id = ct["task_id"]

            # Load child state
            child_state = await child_store.get(child_sid)
            if child_state is None:
                ct["status"] = "error"
                parent_state.setdefault("messages", []).append({
                    "role": "user",
                    "content": f"[系统] 子任务 {task_id} (agent={agent_name}) 的会话状态丢失，无法恢复。",
                })
                continue

            if child_state.get("_session_ended"):
                ct["status"] = "completed"
                continue

            # Rebuild sub-agent
            config_path = _resolve_agent_config(agent_name)
            if config_path is None:
                ct["status"] = "error"
                parent_state.setdefault("messages", []).append({
                    "role": "user",
                    "content": f"[系统] 子任务 {task_id} 的 agent 配置 ({agent_name}) 已删除，无法恢复。",
                })
                continue

            try:
                import yaml as _yaml
                import os as _os
                from pathlib import Path as _Path

                with open(config_path, encoding="utf-8") as f:
                    data = _yaml.safe_load(f)

                ws_root = _Path(_os.environ.get("A4A_WORKSPACE", _Path(__file__).parent.parent.parent.parent))
                config = AgentConfig(**data)
                app_ctx = AppContext(root=ws_root)
                sub_agent = BaseAgent(config, app_context=app_ctx)

                # Inject cancel_event for cascade support
                cancel_evt = self.child_cancel_event(child_sid)
                sub_agent._engine.set_cancel_event(cancel_evt)

                await sub_agent.start()
                try:
                    # Resume from saved state
                    async for _event in sub_agent._engine.astream(child_state):
                        pass  # Drain events — results collected by round_end hook
                finally:
                    await sub_agent.stop()

                ct["status"] = "completed"
                logger.info("Resumed child agent %s (%s) — completed", child_sid, agent_name)
            except Exception as exc:
                ct["status"] = "error"
                parent_state.setdefault("messages", []).append({
                    "role": "user",
                    "content": f"[系统] 恢复子任务 {task_id} (agent={agent_name}) 失败: {exc}",
                })
                logger.exception("Failed to resume child agent %s", child_sid)

            _registry.cancel_events.pop(child_sid, None)

        return parent_state

    @staticmethod
    def build_child_resume_notification(unfinished: list[dict]) -> str:
        """Build notification message listing unfinished child tasks."""
        lines = [
            "[系统] 检测到以下子任务在中断前未完成：",
        ]
        for ct in unfinished:
            lines.append(
                f"  - {ct['task_id']} (agent={ct['agent_name']}, "
                f"session={ct['child_session_id']}, status={ct['status']})"
            )
        lines.append(
            "如需恢复，使用 delegate_task(resume_session=\"<child_session_id>\") 逐个恢复。"
        )
        return "\n".join(lines)

