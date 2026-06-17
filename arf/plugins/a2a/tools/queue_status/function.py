"""queue_status — query running, queued, and completed tasks."""
from arf.plugins.a2a.tools import _registry


async def execute(session_id: str = "") -> dict:
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized"}
    status = await registry.delegator.queue_status(session_id)
    return {"ok": True, **status}
