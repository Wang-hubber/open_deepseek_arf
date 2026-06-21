"""ToolGuardPlugin — unified tool permission + security check.

Deep port: directly extends Plugin base class.

Unified permission model — deny is always enforced first, then effective
session mode determines how allow/ask/unknown are handled:

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
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.core.tool_naming import matches_perm
from arf.session.mode_manager import has_side_effect
from arf.session.types import SessionMode


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

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "pre_action":
            await self._guard(ctx)

    async def _guard(self, ctx: PluginContext) -> None:
        """Unified permission check: deny → mode → allow/ask/unknown."""
        tool_calls = ctx.hook_data.get("_pending_tool_calls", [])
        if not tool_calls:
            return

        effective_mode = ctx.hook_data.get("_effective_mode", SessionMode.ASK)
        tool_annotations: dict = ctx.hook_data.get("_tool_annotations", {})

        for tc in list(tool_calls):
            name = tc.get("name", "")

            # ── deny patterns (always enforced) ──
            for pattern in self._deny_patterns:
                if pattern in name:
                    ctx.emit("guard_block", {
                        "tool_name": name, "reason": f"matches deny_pattern: {pattern}",
                    })
                    ctx.agent.input("tool", {
                        "tool_call_id": tc.get("id", ""),
                        "name": name,
                        "result": f"[blocked] matches deny pattern: {pattern}",
                        "error": "Denied",
                    })
                    self._remove_tool(ctx, tc)
                    break
            else:
                # ── deny list (always enforced, highest priority) ──
                if matches_perm(name, self._deny):
                    ctx.emit("guard_block", {
                        "tool_name": name, "reason": "in deny list",
                    })
                    ctx.agent.input("tool", {
                        "tool_call_id": tc.get("id", ""),
                        "name": name,
                        "result": "[blocked] in deny list",
                        "error": "Denied",
                    })
                    self._remove_tool(ctx, tc)
                    continue

                # ── AUTO: everything else passes ──
                if effective_mode == SessionMode.AUTO:
                    ctx.emit("guard_pass", {"tool_name": name})
                    continue

                # ── ask list ──
                if matches_perm(name, self._ask):
                    if effective_mode == SessionMode.PLAN:
                        if has_side_effect(name, tool_annotations):
                            ctx.emit("guard_block", {
                                "tool_name": name, "reason": "PLAN mode: side-effect tool in ask list",
                            })
                            ctx.agent.input("tool", {
                                "tool_call_id": tc.get("id", ""),
                                "name": name,
                                "result": "[blocked] PLAN mode — read-only",
                                "error": "Side-effect tools are blocked in PLAN mode",
                            })
                            self._remove_tool(ctx, tc)
                        # else: read-only, let it pass
                    else:
                        # ASK mode: pass to approval plugin
                        ctx.hook_data.setdefault("_pending_tool_calls_ask", []).append(tc)
                    continue

                # ── allow list ──
                if matches_perm(name, self._allow):
                    if effective_mode == SessionMode.PLAN:
                        if has_side_effect(name, tool_annotations):
                            ctx.emit("guard_block", {
                                "tool_name": name, "reason": "PLAN mode: side-effect tool in allow list",
                            })
                            ctx.agent.input("tool", {
                                "tool_call_id": tc.get("id", ""),
                                "name": name,
                                "result": "[blocked] PLAN mode — read-only",
                                "error": "Side-effect tools are blocked in PLAN mode",
                            })
                            self._remove_tool(ctx, tc)
                        else:
                            ctx.emit("guard_pass", {"tool_name": name})
                    else:
                        # ASK mode: allow
                        ctx.emit("guard_pass", {"tool_name": name})
                    continue

                # ── unknown tool ──
                if effective_mode == SessionMode.PLAN:
                    ctx.emit("guard_block", {
                        "tool_name": name, "reason": "PLAN mode: unknown tool blocked",
                    })
                    ctx.agent.input("tool", {
                        "tool_call_id": tc.get("id", ""),
                        "name": name,
                        "result": "[blocked] PLAN mode — read-only",
                        "error": "Unknown tools are blocked in PLAN mode",
                    })
                    self._remove_tool(ctx, tc)
                elif self._deny:
                    # ASK mode with deny list active: unknown → block
                    ctx.emit("guard_block", {
                        "tool_name": name, "reason": "not in allow list",
                    })
                    ctx.agent.input("tool", {
                        "tool_call_id": tc.get("id", ""),
                        "name": name,
                        "result": "[blocked] not in allow list",
                        "error": "Denied",
                    })
                    self._remove_tool(ctx, tc)
                else:
                    # ASK mode no deny list: implicit allow-all
                    ctx.emit("guard_pass", {"tool_name": name})

    def _remove_tool(self, ctx: PluginContext, tc: dict) -> None:
        """Remove a tool call from _pending_tool_calls so the engine skips it."""
        ctx.hook_data["_pending_tool_calls"] = [
            t for t in ctx.hook_data.get("_pending_tool_calls", [])
            if t.get("id") != tc.get("id")
        ]


Plugin = ToolGuardPlugin
