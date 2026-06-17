"""await_task — block until a delegated task completes."""
import asyncio
import logging
from arf.plugins.a2a.tools import _registry

logger = logging.getLogger("arf.plugins.a2a.await_task")


async def execute(task_id: str, session_id: str = "", timeout: int = 0) -> dict:
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized"}

    max_wait = min(timeout, registry.max_task_timeout) if timeout else registry.max_task_timeout
    poll_interval = 0.1
    elapsed = 0.0

    while elapsed < max_wait:
        pending = await registry.delegator.get_pending(session_id)
        for p in pending:
            if p.get("task_id") == task_id:
                return {"ok": True, "result": p}
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    return {"ok": False, "error": f"timeout: task {task_id} did not complete within {max_wait:.0f}s"}
