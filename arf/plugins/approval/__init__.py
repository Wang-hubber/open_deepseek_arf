"""ApprovalPlugin — human-in-the-loop tool approval.

Deep port: directly extends Plugin base class.

Uses park/resume: emits approval_required, registers a wait, and returns.
On re-entry (after resolve_wait), checks resolved decisions and filters
_pending_tool_calls.

Optional timeout (config: timeout, default 0 = no timeout): when > 0, a
background task auto-rejects unresolved decisions after timeout seconds.
"""
from __future__ import annotations
import asyncio
from arf.harness.plugin_base import Plugin
from arf.harness.context import PluginContext
from arf.core.tool_naming import matches_perm
from arf.session.types import SessionMode


class ApprovalPlugin(Plugin):
    """Human-in-the-loop approval for tools in ask_list."""

    def __init__(self, name="approval", events=None, config=None):
        events = events or [
            {"hook_name": "before_tools", "event_name": "pre_action", "mode": "blocking"},
        ]
        super().__init__(name=name, events=events, config=config or {})
        self._ask_list = set(self.config.get("ask_list", []))
        self._timeout: float = float(self.config.get("timeout", 0))
        self._decisions: dict[str, str] = {}    # decision_id → wait_id (pending)
        self._results: dict[str, tuple[bool, str]] = {}  # decision_id → (approved, reason)
        self._timers: dict[str, asyncio.Task] = {}      # decision_id → timeout task
        self._agent = None

    async def handle(self, event_name: str, ctx: PluginContext) -> None:
        if event_name == "pre_action":
            await self._check_approval(ctx)

    async def _check_approval(self, ctx: PluginContext) -> None:
        """Check pending tool calls against ask_list. Parks on new approvals."""
        # AUTO mode: skip approval, everything passes
        if ctx.hook_data.get("_effective_mode") == SessionMode.AUTO:
            return

        self._agent = ctx.agent
        tool_calls = ctx.hook_data.get("_pending_tool_calls", [])
        if not tool_calls:
            return

        for tc in list(tool_calls):
            name = tc.get("name", "")
            if not matches_perm(name, self._ask_list):
                continue

            decision_id = f"{ctx.session_id}_{name}_{id(tc)}"

            # Phase 1: apply already-resolved decision
            if decision_id in self._results:
                approved, reason = self._results.pop(decision_id)
                self._decisions.pop(decision_id, None)
                if not approved:
                    ctx.emit("approval_resolved", {
                        "decision_id": decision_id, "approved": False, "reason": reason,
                    })
                    self._block_tool(ctx, tc,
                        result=f"[blocked] {reason}",
                        error=reason)
                else:
                    ctx.emit("approval_resolved", {
                        "decision_id": decision_id, "approved": True,
                    })
                continue

            # Phase 2: new tool needing approval — register wait
            ctx.emit("approval_required", {
                "decision_id": decision_id,
                "tool_name": name,
                "params": tc.get("params", {}),
            })
            wi = ctx.agent.wait("before_tools", f"approval:{decision_id}")
            self._decisions[decision_id] = wi.wait_id

            # Start timeout timer if configured
            if self._timeout > 0:
                self._timers[decision_id] = asyncio.create_task(
                    self._timeout_reject(decision_id, wi.wait_id, ctx.session_id)
                )

    async def _timeout_reject(self, decision_id: str, wait_id: str, session_id: str) -> None:
        """Background task: auto-reject after timeout."""
        await asyncio.sleep(self._timeout)
        # Check if already resolved externally
        if decision_id in self._results or decision_id not in self._decisions:
            return
        self._decisions.pop(decision_id, None)
        self._timers.pop(decision_id, None)
        self._results[decision_id] = (False, "timeout")
        if self._agent:
            self._agent.finish_wait(wait_id)

    @staticmethod
    def _block_tool(ctx: PluginContext, tc: dict, result: str, error: str) -> None:
        """Mark tool as blocked — engine will skip execution and use this result."""
        ctx.hook_data.setdefault("_blocked_results", {})[tc["id"]] = {
            "result": result, "error": error,
        }

    def _remove_tool(self, ctx: PluginContext, tc: dict) -> None:
        """Remove a tool call from _pending_tool_calls so the engine skips it."""
        ctx.hook_data["_pending_tool_calls"] = [
            t for t in ctx.hook_data.get("_pending_tool_calls", [])
            if t.get("id") != tc.get("id")
        ]

    def approve(self, decision_id: str, approved: bool = True) -> bool:
        """External call: resolve an approval decision and finish the wait."""
        # Cancel timeout timer if active
        timer = self._timers.pop(decision_id, None)
        if timer:
            timer.cancel()
        self._results[decision_id] = (approved, "user_denied" if not approved else "")
        wait_id = self._decisions.pop(decision_id, None)
        if wait_id and self._agent:
            self._agent.finish_wait(wait_id)
            return True
        return False


Plugin = ApprovalPlugin
