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
        return {"pre_dispatch": "blocking"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if ctx.current_step != "execute_tools":
            return

        tool_calls = ctx.state.get("_pending_tool_calls", [])
        for tc in tool_calls:
            name = tc.get("name", "")
            if name not in self._ask_list:
                continue

            decision_id = f"{ctx.session_id}_{name}_{id(tc)}"
            ctx.hook_data.setdefault("approval_requests", []).append({
                "decision_id": decision_id, "tool_name": name,
                "params": tc.get("params", {}),
            })

            # Check if approve() was already called before the hook ran
            # (approval_required event is yielded before _fire_blocking).
            pre_resolved = self._results.pop(decision_id, None)
            if pre_resolved is not None:
                if not pre_resolved:
                    raise ApprovalDenied(f"User denied {name}")
                continue  # already approved, skip waiting

            evt = asyncio.Event()
            self._pending[decision_id] = evt
            try:
                await asyncio.wait_for(evt.wait(), timeout=self._timeout)
            except asyncio.TimeoutError:
                self._pending.pop(decision_id, None)
                raise ApprovalTimeout(f"Approval timed out for {name}")
            approved = self._results.pop(decision_id, False)
            if not approved:
                raise ApprovalDenied(f"User denied {name}")

    def approve(self, decision_id: str, approved: bool = True) -> bool:
        self._results[decision_id] = approved
        evt = self._pending.pop(decision_id, None)
        if evt:
            evt.set()
            return True
        return False
