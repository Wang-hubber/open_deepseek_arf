"""ApprovalPlugin — human-in-the-loop tool approval.

Deep port: directly extends Plugin base class.
"""
from __future__ import annotations
import asyncio
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext


class ApprovalPlugin(Plugin):
    """Human-in-the-loop approval for tools in ask_list."""

    def __init__(self, name="approval", events=None, config=None):
        events = events or [
            {"hook_name": "before_tools", "event_name": "pre_action", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._timeout = self.config.get("timeout", 60.0)
        self._ask_list = set(self.config.get("ask_list", []))
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, bool] = {}

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "pre_action":
            await self._check_approval(ctx)

    async def _check_approval(self, ctx: PluginContext) -> None:
        """Check pending tool calls against ask_list. Blocks on approval."""
        tool_calls = ctx.hook_data.get("_pending_tool_calls", [])
        if not tool_calls:
            return

        for tc in tool_calls:
            name = tc.get("name", "")
            if name not in self._ask_list:
                continue

            decision_id = f"{ctx.session_id}_{name}_{id(tc)}"
            ctx.emit("approval_required", {
                "decision_id": decision_id,
                "tool_name": name,
                "params": tc.get("params", {}),
            })

            # Wait for approve() to be called
            evt = asyncio.Event()
            self._pending[decision_id] = evt
            try:
                await asyncio.wait_for(evt.wait(), timeout=self._timeout)
            except asyncio.TimeoutError:
                self._pending.pop(decision_id, None)
                ctx.emit("approval_resolved", {
                    "decision_id": decision_id, "approved": False, "reason": "timeout",
                })
                # Inject error tool result and deny
                ctx.agent.input("tool", {
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "result": "[blocked] timeout",
                    "error": "Approval timed out",
                })
                raise RuntimeError(f"Approval timed out for tool '{name}'")
            finally:
                self._results.pop(decision_id, None)

            approved = self._results.pop(decision_id, False)
            if not approved:
                ctx.emit("approval_resolved", {
                    "decision_id": decision_id, "approved": False, "reason": "user_denied",
                })
                ctx.agent.input("tool", {
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "result": "[blocked] user denied",
                    "error": "User denied",
                })
                raise RuntimeError(f"Tool '{name}' denied by user")

            ctx.emit("approval_resolved", {
                "decision_id": decision_id, "approved": True,
            })

    def approve(self, decision_id: str, approved: bool = True) -> bool:
        self._results[decision_id] = approved
        evt = self._pending.pop(decision_id, None)
        if evt:
            evt.set()
            return True
        return False


Plugin = ApprovalPlugin
