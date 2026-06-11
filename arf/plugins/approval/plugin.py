"""ApprovalPlugin — human-in-the-loop approval for tools in ask_list."""
import asyncio
from arf.core.plugin_context import PluginContext


class ApprovalTimeout(Exception):
    pass


class ApprovalDenied(Exception):
    pass


class ApprovalPlugin:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._timeout = cfg.get("timeout", 60.0)
        self._ask_list = set(cfg.get("ask_list", []))
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, bool] = {}

    @property
    def name(self) -> str:
        return "approval"

    @property
    def hooks(self) -> dict[str, str]:
        return {"pre_action": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if ctx.current_step != "execute_tools":
            return

        tool_calls = ctx.state.get("_pending_tool_calls", [])
        denied: list[dict] = []

        for tc in tool_calls:
            name = tc.get("name", "")
            if name not in self._ask_list:
                continue

            decision_id = f"{ctx.session_id}_{name}_{id(tc)}"
            ctx.hook_data.setdefault("approval_requests", []).append({
                "decision_id": decision_id, "tool_name": name,
                "params": tc.get("params", {}),
            })

            # Check if approve() was already called (defensive, should be rare)
            pre_resolved = self._results.pop(decision_id, None)
            if pre_resolved is not None:
                if not pre_resolved:
                    denied.append(tc)
                continue

            ctx.emit("approval_required", {
                "decision_id": decision_id,
                "tool_name": name,
                "params": tc.get("params", {}),
            })

            evt = asyncio.Event()
            self._pending[decision_id] = evt
            try:
                await asyncio.wait_for(evt.wait(), timeout=self._timeout)
            except asyncio.TimeoutError:
                self._pending.pop(decision_id, None)
                denied.append(tc)
                ctx.emit("approval_resolved", {
                    "decision_id": decision_id,
                    "approved": False,
                    "reason": "timeout",
                })
                continue

            approved = self._results.pop(decision_id, False)
            if approved:
                ctx.emit("approval_resolved", {
                    "decision_id": decision_id,
                    "approved": True,
                })
            else:
                denied.append(tc)
                ctx.emit("approval_resolved", {
                    "decision_id": decision_id,
                    "approved": False,
                    "reason": "user_denied",
                })

        # Clean up state for denied/timed-out tool calls so the API message
        # format stays valid (tool_calls must be followed by tool messages).
        if denied:
            for tc in denied:
                event_data = {
                    "tool_name": tc.get("name", ""),
                    "id": tc.get("id", ""),
                }
                ctx.emit("tool_call_start", {
                    **event_data,
                    "arguments": tc.get("params", {}),
                })
                ctx.inject_engine_event("tool_call_start", {
                    **event_data,
                    "arguments": tc.get("params", {}),
                })
                ctx.emit("tool_call_end", {
                    **event_data,
                    "success": False,
                    "blocked": True,
                    "error": "Blocked: user denied",
                })
                ctx.inject_engine_event("tool_call_end", {
                    **event_data,
                    "success": False,
                    "blocked": True,
                    "error": "Blocked: user denied",
                })
                ctx.state["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": "[blocked] user denied",
                })
            ctx.state["_pending_tool_calls"] = []
            raise ApprovalDenied("Tool execution denied by user")

    def approve(self, decision_id: str, approved: bool = True) -> bool:
        self._results[decision_id] = approved
        evt = self._pending.pop(decision_id, None)
        if evt:
            evt.set()
            return True
        return False
