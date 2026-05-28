"""planner — task decomposition via system_model."""
import json


async def execute(task: str = "", _engine=None) -> dict:
    """Decompose a task into ordered steps using the system model."""
    if not task or not task.strip():
        return {"error": "task must be a non-empty string"}

    try:
        messages = []
        if _engine is not None and hasattr(_engine, '_last_state'):
            msgs = _engine._last_state.get("messages", [])
            user_msgs = [m.get("content", "") for m in msgs[-6:] if m.get("role") == "user"]
            messages = user_msgs[-3:]

        context = "\n".join(messages)

        prompt = (
            "You are a task planner. Given a user's goal and conversation context, "
            "decompose the goal into an ordered list of concrete, actionable steps.\n\n"
            f"## Conversation Context\n{context}\n\n"
            f"## Goal\n{task}\n\n"
            "## Instructions\n"
            "1. Produce 2-7 steps in logical order\n"
            "2. Each step must be a single action one tool can accomplish\n"
            "3. Return ONLY valid JSON:\n"
            '{"steps": [{"index": 1, "description": "...", "tool_hint": "..."}]}\n'
        )

        if _engine is not None and hasattr(_engine, '_call_model'):
            response = await _engine._call_model(
                [{"role": "user", "content": prompt}],
                model_name=getattr(_engine, '_system_model_name', ''),
            )
            content = response.get("content", "") if isinstance(response, dict) else str(response)
            try:
                plan = json.loads(content)
            except json.JSONDecodeError:
                plan = {"steps": [{"index": 1, "description": task}]}

            return {
                "ok": True,
                "task": task,
                "steps": plan.get("steps", []),
                "count": len(plan.get("steps", [])),
            }

        return {"ok": True, "task": task, "steps": [{"index": 1, "description": task}], "count": 1, "note": "no engine — fallback plan"}
    except Exception as e:
        return {"error": str(e)}
