"""A2ATaskLifecycle — sub-agent completion with delegator integration."""
from __future__ import annotations

import logging
from typing import Any

from arf.core.plugin_context import PluginContext
from arf.core.protocols.task_lifecycle import DefaultTaskLifecycle

logger = logging.getLogger("arf.plugins.a2a_subagents.task_lifecycle")


class A2ATaskLifecycle(DefaultTaskLifecycle):
    """Calls delegator.complete() in addition to emitting EventBus event.

    Detects workspace file changes via pre/post snapshot diff and includes
    tool_calls_summary from the shared reference, so that task completion
    results are complete even when A2ATaskLifecycle.complete() fires before
    the post-execution workspace comparison in _dispatch_external.
    """

    def __init__(self, event_bus, delegator,
                 parent_sid: str = "", child_sid: str = "", task_id: str = "",
                 pre_snapshot: dict[str, str] | None = None,
                 tool_calls_ref: list[dict] | None = None) -> None:
        super().__init__(event_bus)
        self._delegator = delegator
        self._parent_sid = parent_sid
        self._child_sid = child_sid
        self._task_id = task_id
        self._pre_snapshot = pre_snapshot
        self._tool_calls_ref = tool_calls_ref

    async def complete(
        self, result: str, files_changed: dict[str, list[str]],
        confidence: float, notes: str, ctx: PluginContext,
    ) -> dict:
        r = await super().complete(
            result, files_changed, confidence, notes, ctx)

        if self._delegator and self._parent_sid and self._task_id:
            complete_result: dict[str, Any] = {
                "ok": True, "content": result,
                "turn_count": ctx.state.get("current_turn", 0),
                "gate_exceeded": False,
            }
            if files_changed:
                complete_result["file_changes"] = files_changed

            # Compute workspace file changes from snapshot diff and merge
            # with tool-provided files_changed.
            if self._pre_snapshot is not None:
                from arf.plugins.a2a_subagents.tools.delegate_task.function import (
                    _snapshot_workspace,
                )
                ws_dir = getattr(ctx, 'workspace_dir', '') or '.'
                post_snapshot = _snapshot_workspace(ws_dir) if ws_dir != '.' else {}
                added = [p for p in post_snapshot if p not in self._pre_snapshot]
                modified = [
                    p for p in post_snapshot
                    if p in self._pre_snapshot
                    and post_snapshot[p] != self._pre_snapshot[p]
                ]
                deleted = [p for p in self._pre_snapshot if p not in post_snapshot]
                ws_file_changes: dict[str, list[str]] = {}
                if added or modified or deleted:
                    ws_file_changes = {
                        "added": sorted(added),
                        "modified": sorted(modified),
                        "deleted": sorted(deleted),
                    }
                # Merge workspace-detected changes with tool-provided changes
                merged_file_changes: dict[str, list[str]] = {}
                if files_changed:
                    for key in ("added", "modified", "deleted"):
                        merged_file_changes[key] = sorted(
                            set(files_changed.get(key, []))
                            | set(ws_file_changes.get(key, []))
                        )
                elif ws_file_changes:
                    merged_file_changes = ws_file_changes
                if merged_file_changes:
                    complete_result["file_changes"] = merged_file_changes

            if self._tool_calls_ref is not None:
                complete_result["tool_calls_summary"] = list(self._tool_calls_ref)

            try:
                await self._delegator.complete(
                    self._parent_sid, self._task_id, complete_result)
            except Exception:
                logger.exception(
                    "Failed to complete delegator task %s", self._task_id)
        return r
