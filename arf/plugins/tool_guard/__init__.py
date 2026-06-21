"""ToolGuardPlugin — unified tool permission + security check.

Deep port: directly extends Plugin base class.

Three-layer enforcement:

1. deny_patterns   — regex on serialized params (always enforced)
2. deny list        — tool name blacklist (always enforced)
3. sandbox_check    — path traversal scan (always enforced, configurable)
4. mode + allow/ask — mode-dependent allow/ask/unknown handling

Unified permission model:

  ┌──────────┬──────────┬──────────────┬──────────────┬──────────┐
  │ mode     │ deny     │ allow        │ ask          │ unknown  │
  ├──────────┼──────────┼──────────────┼──────────────┼──────────┤
  │ AUTO     │ reject   │ pass         │ pass (no HITL)│ pass    │
  │ PLAN     │ reject   │ readOnlyHint │ readOnlyHint │ reject   │
  │ ASK      │ reject   │ pass         │ pass (→HITL) │ reject*  │
  └──────────┴──────────┴──────────────┴──────────────┴──────────┘

  * ASK unknown: reject if deny list is active, otherwise pass (implicit
    allow-all when no deny list is configured).
"""
from __future__ import annotations
import json
import logging
import os
import re
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.core.tool_naming import matches_perm
from arf.session.mode_manager import has_side_effect
from arf.session.types import SessionMode

logger = logging.getLogger(__name__)


class ToolGuardPlugin(Plugin):
    """Blocks denied tools, allows permitted ones. Guards before_tools checkpoint."""

    def __init__(self, name="tool_guard", events=None, config=None):
        events = events or [
            {"hook_name": "before_tools", "event_name": "pre_action", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._deny: set[str] = set(self.config.get("deny", []))
        self._ask: set[str] = set(self.config.get("ask", []))
        self._allow: set[str] = set(self.config.get("allow", []))
        self._deny_patterns: list[str] = self.config.get("deny_patterns", [])
        self._sandbox_check: bool = self.config.get("sandbox_check", True)

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "pre_action":
            await self._guard(ctx)

    async def _guard(self, ctx: PluginContext) -> None:
        """Unified permission check: deny → sandbox → mode → allow/ask/unknown."""
        tool_calls = ctx.hook_data.get("_pending_tool_calls", [])
        if not tool_calls:
            return

        effective_mode = ctx.hook_data.get("_effective_mode", SessionMode.ASK)
        tool_defs: dict = ctx.hook_data.get("_tool_defs", {})

        for tc in list(tool_calls):
            name = tc.get("name", "")
            params: dict = tc.get("params", {})

            # ── Layer 1: deny_patterns — param content regex (always enforced) ──
            params_str = json.dumps(params, ensure_ascii=False) if params else ""
            for pattern in self._deny_patterns:
                if re.search(pattern, params_str, re.IGNORECASE):
                    ctx.emit(event_type="guard_block", data={
                        "tool_name": name,
                        "reason": f"matches deny_pattern: {pattern}",
                    })
                    self._block_tool(ctx, tc,
                        result=f"[blocked] matches deny pattern: {pattern}",
                        error="Denied")
                    break
            else:
                # ── Layer 2: deny list (always enforced) ──
                if matches_perm(name, self._deny):
                    ctx.emit(event_type="guard_block", data={
                        "tool_name": name, "reason": "in deny list",
                    })
                    self._block_tool(ctx, tc,
                        result="[blocked] in deny list",
                        error="Denied")
                    continue

                # ── Layer 3: sandbox path check (always enforced when enabled) ──
                sandbox_blocked = False
                if self._sandbox_check:
                    allow_paths: list[str] = ctx.hook_data.get("_allow_paths", [])
                    if not allow_paths:
                        logger.warning(
                            "Sandbox enabled but allow_paths is empty. "
                            "Set allow_paths in agent.yaml to restrict file access.")
                    else:
                        td = tool_defs.get(name)
                        if td is None:
                            logger.warning(
                                "Sandbox enabled but tool '%s' not found in _tool_defs.", name)
                        else:
                            param_props = td.get("parameters", {}).get("properties", {})
                            for key, value in params.items():
                                is_path = param_props.get(key, {}).get("format") == "path"
                                if is_path and isinstance(value, str):
                                    if not self._path_in_allowlist(value, allow_paths):
                                        ctx.emit(event_type="guard_block", data={
                                            "tool_name": name,
                                            "reason": f"sandbox: {key} outside allow_paths",
                                        })
                                        self._block_tool(ctx, tc,
                                            result=f"[blocked] sandbox: '{value}' not in allow_paths",
                                            error="Sandbox violation")
                                        sandbox_blocked = True
                                        break
                if sandbox_blocked:
                    continue

                # ── Layer 4: AUTO — everything else passes ──
                if effective_mode == SessionMode.AUTO:
                    ctx.emit(event_type="guard_pass", data={"tool_name": name})
                    continue

                # ── ask list ──
                if matches_perm(name, self._ask):
                    if effective_mode == SessionMode.PLAN:
                        if has_side_effect(name, tool_defs):
                            ctx.emit(event_type="guard_block", data={
                                "tool_name": name, "reason": "PLAN mode: side-effect tool in ask list",
                            })
                            self._block_tool(ctx, tc,
                                result="[blocked] PLAN mode — read-only",
                                error="Side-effect tools are blocked in PLAN mode")
                    else:
                        ctx.hook_data.setdefault("_pending_tool_calls_ask", []).append(tc)
                    continue

                # ── allow list ──
                if matches_perm(name, self._allow):
                    if effective_mode == SessionMode.PLAN:
                        if has_side_effect(name, tool_defs):
                            ctx.emit(event_type="guard_block", data={
                                "tool_name": name, "reason": "PLAN mode: side-effect tool in allow list",
                            })
                            self._block_tool(ctx, tc,
                                result="[blocked] PLAN mode — read-only",
                                error="Side-effect tools are blocked in PLAN mode")
                        else:
                            ctx.emit(event_type="guard_pass", data={"tool_name": name})
                    else:
                        ctx.emit(event_type="guard_pass", data={"tool_name": name})
                    continue

                # ── unknown tool ──
                if effective_mode == SessionMode.PLAN:
                    ctx.emit(event_type="guard_block", data={
                        "tool_name": name, "reason": "PLAN mode: unknown tool blocked",
                    })
                    self._block_tool(ctx, tc,
                        result="[blocked] PLAN mode — read-only",
                        error="Unknown tools are blocked in PLAN mode")
                elif self._deny:
                    ctx.emit(event_type="guard_block", data={
                        "tool_name": name, "reason": "not in allow list",
                    })
                    self._block_tool(ctx, tc,
                        result="[blocked] not in allow list",
                        error="Denied")
                else:
                    ctx.emit(event_type="guard_pass", data={"tool_name": name})

    @staticmethod
    def _path_in_allowlist(value: str, allow_paths: list[str]) -> bool:
        """Return True if *value* resolves to a path within any allow_paths entry."""
        normalized = os.path.normpath(value)
        for ap in allow_paths:
            ap_norm = os.path.normpath(ap)
            if normalized == ap_norm or normalized.startswith(ap_norm + os.sep):
                return True
        return False

    @staticmethod
    def _block_tool(ctx: PluginContext, tc: dict, result: str, error: str) -> None:
        """Write blocked result and remove from _pending_tool_calls.

        Blocked tools are removed so subsequent plugins (e.g. approval) skip them.
        The engine processes _blocked_results alongside _pending_tool_calls so the
        LLM still sees the blocked result as a tool response.
        """
        ctx.hook_data.setdefault("_blocked_results", {})[tc["id"]] = {
            "result": result, "error": error,
        }
        ctx.hook_data["_pending_tool_calls"] = [
            t for t in ctx.hook_data.get("_pending_tool_calls", [])
            if t.get("id") != tc.get("id")
        ]


Plugin = ToolGuardPlugin
