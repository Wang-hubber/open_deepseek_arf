"""A2A Plugin — hook-driven task delegation for AgentHarness.

Hooks:
  session_start — capture parent config for inline-like sub-agents + resume rebuild
  before_tools  — inject session_id into delegate_task tool params
  before_model  — safety net: inject completed task results into parent messages
  after_round   — child cleanup + parent cascade cancel
"""
from __future__ import annotations

import logging
from pathlib import Path

from arf.communication.queued_delegator import QueuedTaskDelegator
from arf.harness.context import PluginContext
from arf.harness.plugin_base import Plugin
from arf.plugins.a2a_subagents.config import A2APluginConfig
from arf.plugins.a2a_subagents.tools import _registry

logger = logging.getLogger("arf.plugins.a2a_subagents")

_DEFAULT_EVENTS = [
    {"hook_name": "session_start", "event_name": "init", "mode": "blocking"},
    {"hook_name": "before_tools", "event_name": "inject_session_id", "mode": "blocking"},
    {"hook_name": "before_model", "event_name": "inject_results", "mode": "blocking"},
    {"hook_name": "after_round", "event_name": "cleanup", "mode": "blocking"},
]


class Plugin(Plugin):
    """Harness plugin for A2A task delegation.

    Tools are auto-loaded by McpClientManager from arf/plugins/a2a_subagents/tools/.
    No extra setup needed — enabling the plugin in harness.yaml is enough.
    """

    def __init__(self, name: str, events: list[dict] | None = None, config: dict | None = None) -> None:
        super().__init__(name=name, events=events or _DEFAULT_EVENTS, config=config or {})
        cfg = A2APluginConfig(**(config or {}))

        self._max_task_timeout = cfg.max_task_timeout
        self._child_resume = cfg.child_resume

        # Initialize delegator singleton — only one per process
        if _registry.delegator is None:
            _registry.delegator = QueuedTaskDelegator(
                max_concurrent=cfg.max_concurrent_tasks,
            )
        _registry.max_task_timeout = self._max_task_timeout
        _registry.child_resume = self._child_resume

        # Cascade cancel tracking
        if not _registry.cancel_events:
            _registry.cancel_events = {}

        # Parent park tracking — used by runner to wake parent on sub-agent completion
        if not hasattr(_registry, "_parent_wait_ids"):
            _registry._parent_wait_ids: dict[str, str] = {}
        if not hasattr(_registry, "parent_harness"):
            _registry.parent_harness = None

    # ==================================================================
    # handle — dispatch by event_name
    # ==================================================================

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "init":
            await self._on_init(ctx)
        elif event_name == "inject_session_id":
            await self._on_inject_session_id(ctx)
        elif event_name == "inject_results":
            await self._on_inject_results(ctx)
        elif event_name == "cleanup":
            await self._on_cleanup(ctx)

    # ==================================================================
    # init — capture parent config for inline-like mode
    # ==================================================================

    async def _on_init(self, ctx: PluginContext) -> None:
        """Capture parent harness config so delegate_task can create inline-like sub-agents."""
        agent = ctx.agent
        harness_ref = ctx.hook_data.get("_harness_ref", {})

        _registry.current_session_id = ctx.session_id
        _registry.data_dir = ctx.data_dir

        # Store parent harness so runner can wake it when sub-agent completes
        _registry.parent_harness = harness_ref.get("harness")

        _registry.parent_config = {
            "call_model": agent._call_model,
            "stream_model": agent._stream_model,
            "model_config": agent.state.model_config,
            "tool_manager": harness_ref.get("tool_manager"),
            "plugins": harness_ref.get("plugins", []),
            "agent_config": harness_ref.get("agent_config"),
            "max_turns": harness_ref.get("max_turns", 50),
            "data_dir": ctx.data_dir,
            "event_bus": ctx._event_bus,
        }

        # Resume rebuild: re-establish parent_wait_ids for subagent waits
        pending_resume = ctx.hook_data.get("_pending_resume", [])
        for wi in pending_resume:
            if wi.resume_key.startswith("subagent:"):
                _registry._parent_wait_ids[ctx.session_id] = wi.wait_id
                _registry.parent_harness = ctx.hook_data.get("_harness_ref", {}).get("harness")
                logger.info("Rebuilt subagent wait %s for session %s", wi.resume_key, ctx.session_id)

    # ==================================================================
    # inject_session_id — inject session_id into delegate_task params
    # ==================================================================

    async def _on_inject_session_id(self, ctx: PluginContext) -> None:
        """Inject session_id into ALL a2a_subagents tool params before execution.

        delegate_task, queue_status, cancel_task, cancel_held all need
        session_id to scope their operations to the current session.
        Without it, queue_status looks up _sessions[""] and returns all zeros.
        """
        _a2a_tools = {"delegate_task", "queue_status", "cancel_task", "cancel_held"}
        for tc in ctx.hook_data.get("_pending_tool_calls", []):
            local = tc.get("name", "").rsplit("__", 1)[-1]
            if local in _a2a_tools:
                tc.setdefault("params", {})["session_id"] = ctx.session_id

    # ==================================================================
    # inject_results — inject completed task results into parent messages
    # ==================================================================

    async def _on_inject_results(self, ctx: PluginContext) -> None:
        """Safety net: inject any results that arrived between wake_parent and before_model.

        No wait registration here — delegate_task tool already registered
        the wait via _register_wait at before_tools.
        """
        _registry.current_session_id = ctx.session_id

        parent_sid = ctx.session_id
        delegator = _registry.delegator
        if delegator is None:
            return

        # Only inject results (safety net for race between wake and before_model)
        pending = await delegator.get_pending(parent_sid)
        if pending:
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

                ctx.agent.input(role="user", content=content)
                logger.info(
                    "Injected A2A result for %s into parent session %s (safety net)",
                    task_id, parent_sid,
                )

    # ==================================================================
    # cleanup — child status update + parent cascade cancel
    # ==================================================================

    async def _on_cleanup(self, ctx: PluginContext) -> None:
        """Child round end: update status. Parent round end: cascade cancel."""
        _registry.current_session_id = ctx.session_id

        child_sid = ctx.session_id
        parent_sid, task_id = self._parse_child_session(child_sid)

        if parent_sid is not None:
            # Child session ending
            await self._update_child_status(ctx, parent_sid, child_sid, "completed")
            _registry.cancel_events.pop(child_sid, None)
        else:
            # Parent session — check if this is the final round
            # Cascade cancel if children are still running
            child_tasks = await self._get_child_tasks(ctx, child_sid)
            if child_tasks:
                await self._cascade_cancel(ctx)

    async def _cascade_cancel(self, ctx: PluginContext) -> None:
        """Set all registered child cancel_events."""
        parent_sid = ctx.session_id
        child_sids = await self._get_child_task_sids(ctx, parent_sid)

        for child_sid, event in list(_registry.cancel_events.items()):
            if child_sid not in child_sids:
                continue
            if not event.is_set():
                event.set()
                await self._update_child_status(ctx, parent_sid, child_sid, "cancelled")
            _registry.cancel_events.pop(child_sid, None)

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

    async def _update_child_status(
        self, ctx: PluginContext, parent_sid: str,
        child_session_id: str, status: str,
    ) -> None:
        """Update a child_tasks entry's status in the parent state store."""
        from arf.engine.checkpoint import FileStateStore
        try:
            parent_store = FileStateStore(ctx.data_dir)
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

    async def _get_child_tasks(self, ctx: PluginContext, session_id: str) -> list[dict]:
        """Get child_tasks list from state store."""
        from arf.engine.checkpoint import FileStateStore
        try:
            store = FileStateStore(ctx.data_dir)
            state = await store.get(session_id)
            if state:
                return state.get("child_tasks", [])
        except Exception:
            pass
        return []

    async def _get_child_task_sids(self, ctx: PluginContext, parent_sid: str) -> set[str]:
        """Get set of child session IDs for a parent."""
        child_tasks = await self._get_child_tasks(ctx, parent_sid)
        return {ct["child_session_id"] for ct in child_tasks}

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

        result_file = result.get("result_file")
        if result_file:
            parts.append(f"[Full result: {result_file}]")

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

        ws = ctx.hook_data.get("_workspace_dir", ".")
        data_dir = ctx.data_dir
        conflict_dir = Path(data_dir) / parent_sid / "conflicts" / task_id
        conflict_dir.mkdir(parents=True, exist_ok=True)

        files_dir = conflict_dir / "files"
        for path in conflict_paths:
            src = Path(ws) / path
            if src.exists():
                dst = files_dir / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))

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
