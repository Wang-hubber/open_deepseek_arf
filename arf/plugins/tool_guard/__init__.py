"""ToolGuardPlugin — unified tool permission + security check.

Deep port: directly extends Plugin base class.
"""
from __future__ import annotations
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext


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
        """Check tool calls against deny/allow lists."""
        tool_calls = ctx.hook_data.get("_pending_tool_calls", [])
        if not tool_calls:
            return

        for tc in tool_calls:
            name = tc.get("name", "")

            # Check deny patterns
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
                    raise RuntimeError(f"Tool '{name}' denied by guard")

            # Explicit deny overrides allow
            if name in self._deny:
                ctx.emit("guard_block", {
                    "tool_name": name, "reason": "in deny list",
                })
                ctx.agent.input("tool", {
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "result": "[blocked] in deny list",
                    "error": "Denied",
                })
                raise RuntimeError(f"Tool '{name}' denied by guard")

            # Send to ask list (approval plugin handles this)
            if name in self._ask:
                ctx.hook_data.setdefault("_pending_tool_calls_ask", []).append(tc)
                continue

            # Allow known tools
            if name in self._allow or not self._deny:
                ctx.emit("guard_pass", {"tool_name": name})
                continue

            # Unknown tool with deny list active: block
            if self._deny and name not in self._allow:
                ctx.emit("guard_block", {
                    "tool_name": name, "reason": "not in allow list",
                })
                ctx.agent.input("tool", {
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "result": "[blocked] not in allow list",
                    "error": "Denied",
                })
                raise RuntimeError(f"Tool '{name}' not allowed")


Plugin = ToolGuardPlugin
