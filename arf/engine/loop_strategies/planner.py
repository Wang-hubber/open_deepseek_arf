"""PromptBasedPlanner — model-driven plan generation and revision."""
from arf.core.state import AgentState, TurnContext


class PromptBasedPlanner:
    def __init__(self, model_call: callable) -> None:
        self._call_model = model_call

    async def generate_plan(self, task: str, context: TurnContext, tools: list[dict]) -> dict:
        return {"id": "plan_1", "goal": task, "steps": [], "current_step_index": 0, "status": "draft"}

    async def update_progress(self, plan: dict, completed_step: dict, result) -> dict:
        plan["current_step_index"] = plan.get("current_step_index", 0) + 1
        if plan["current_step_index"] >= len(plan.get("steps", [])):
            plan["status"] = "completed"
        return plan

    async def detect_divergence(self, plan: dict, state: AgentState) -> dict:
        return {"diverged": False, "reason": "", "affected_steps": [], "suggested_revision": ""}

    async def revise(self, plan: dict, divergence: dict, context: TurnContext) -> dict:
        plan["status"] = "revising"
        return plan
