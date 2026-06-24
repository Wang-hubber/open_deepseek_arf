"""cancel_task — remove a queued task from the delegation queue."""
from arf.plugins.a2a_subagents.tools import _registry


async def execute(task_id: str, session_id: str = "") -> dict:
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized"}
    cancelled = await registry.delegator.cancel(session_id or registry.current_session_id, task_id)
    return {"ok": True, "cancelled": cancelled}
