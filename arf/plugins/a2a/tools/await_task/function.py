"""await_task — block until a delegated task completes."""
import asyncio
import time
import logging
from arf.plugins.a2a.tools import _registry

logger = logging.getLogger("arf.plugins.a2a.await_task")


async def execute(task_id: str, session_id: str = "", timeout: int = 0) -> dict:
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized"}

    max_wait = min(timeout, registry.max_task_timeout) if timeout else registry.max_task_timeout
    poll_interval = 0.1
    deadline = time.monotonic() + max_wait

    while time.monotonic() < deadline:
        # Non-consuming read — does not interfere with pre_action's get_pending()
        result = await registry.delegator.get_task_result(session_id, task_id)
        if result is not None:
            return {"ok": True, "result": result}
        await asyncio.sleep(poll_interval)

    return {"ok": False, "error": f"timeout: task {task_id} did not complete within {max_wait:.0f}s"}
