"""PlanSolvePlugin — contract validation for plan_solve tool family."""
import json
from pathlib import Path

from arf.core.plugin_context import PluginContext


class PlanSolvePlugin:
    """Enforce plan execution contract via pre_action and round_start hooks.

    pre_action: validates plan_dispatch (deps done) and plan_summarize (all done).
    round_start: detects unfinished plan and emits plan_resumable event.

    Validation failures inject a tool_result error — the model sees it and
    decides next steps. No exceptions raised for expected check failures.
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._workspace_dir = cfg.get("workspace_dir", "")

    @property
    def name(self) -> str:
        return "plan_solve"

    @property
    def hooks(self) -> dict[str, str]:
        return {
            "pre_action": "blocking",
            "round_start": "side",
        }

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        if hook_name == "pre_action":
            await self._on_pre_action(ctx)
        elif hook_name == "round_start":
            await self._on_round_start(ctx)

    async def _on_pre_action(self, ctx: PluginContext) -> None:
        ws = ctx.workspace_dir or self._workspace_dir
        if not ws:
            return
        plan_file = Path(ws) / "plan.json"
        if not plan_file.exists():
            return

        plan = json.loads(plan_file.read_text())
        tool_calls = ctx.state.get("_pending_tool_calls", [])
        removed_ids = set()

        for tc in tool_calls:
            name = tc.get("name", "")
            params = tc.get("params", {})
            tc_id = tc.get("id", "")

            if name in ("plan_solve__plan_dispatch", "plan_dispatch"):
                step_index = params.get("step_index")
                if step_index is not None:
                    step = self._find_step(plan, step_index)
                    if step is None:
                        self._inject_error(tc_id, ctx, f"step {step_index} not found in plan")
                        removed_ids.add(tc_id)
                        continue
                    blocked = [d for d in step.get("depends_on", [])
                               if self._step_status(plan, d) != "done"]
                    if blocked:
                        self._inject_error(tc_id, ctx,
                            f"step {step_index} is blocked: depends on steps {blocked}")
                        removed_ids.add(tc_id)
                    elif step["status"] != "pending":
                        self._inject_error(tc_id, ctx,
                            f"step {step_index} is already {step['status']}")
                        removed_ids.add(tc_id)

            elif name in ("plan_solve__plan_summarize", "plan_summarize"):
                pending = [s["index"] for s in plan.get("steps", [])
                           if s["status"] in ("pending", "running")]
                if pending:
                    self._inject_error(tc_id, ctx,
                        f"cannot summarize: steps {pending} still pending/running")
                    removed_ids.add(tc_id)

        if removed_ids:
            ctx.state["_pending_tool_calls"] = [
                t for t in tool_calls if t.get("id") not in removed_ids
            ]

    async def _on_round_start(self, ctx: PluginContext) -> None:
        ws = ctx.workspace_dir or self._workspace_dir
        if not ws:
            return
        plan_file = Path(ws) / "plan.json"
        if not plan_file.exists():
            return

        plan = json.loads(plan_file.read_text())
        if plan.get("status") != "executing":
            return

        pending_steps = [s for s in plan.get("steps", []) if s["status"] in ("pending", "running")]
        completed_steps = [s for s in plan.get("steps", []) if s["status"] == "done"]
        ctx.emit("plan_resumable", {
            "plan_id": plan["plan_id"],
            "task": plan["task"],
            "pending_steps": [{"index": s["index"], "description": s["description"], "status": s["status"]}
                              for s in pending_steps],
            "completed_steps": [{"index": s["index"], "description": s["description"]}
                                for s in completed_steps],
        })

    def _find_step(self, plan: dict, step_index: int) -> dict | None:
        for s in plan.get("steps", []):
            if s["index"] == step_index:
                return s
        return None

    def _step_status(self, plan: dict, step_index: int) -> str:
        s = self._find_step(plan, step_index)
        return s["status"] if s else "unknown"

    def _inject_error(self, tc_id: str, ctx: PluginContext, error_msg: str) -> None:
        ctx.state.setdefault("messages", []).append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": json.dumps({"ok": False, "error": error_msg}),
        })
