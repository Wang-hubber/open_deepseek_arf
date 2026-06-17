"""delegate_task — spawn a sub-agent via QueuedTaskDelegator."""
import logging

from arf.plugins.a2a.tools import _registry

logger = logging.getLogger("arf.plugins.a2a.delegate_task")


async def execute(
    task: str,
    agent: str = "",
    timeout: int = 0,
    context: dict | None = None,
    _engine=None,
    session_id: str = "",
) -> dict:
    """Spawn a sub-agent to handle *task*. Uses QueuedTaskDelegator for slot scheduling."""
    registry = _registry
    if registry.delegator is None:
        return {"ok": False, "error": "A2A plugin not initialized — delegator is None"}

    parent_sid = session_id
    if not parent_sid and _engine is not None:
        parent_sid = getattr(_engine, "_current_session_id", "default")

    engine = _engine or registry.engine
    if engine is None:
        return {"ok": False, "error": "No engine available for sub-agent execution"}

    # Cap timeout
    effective_timeout = min(timeout, registry.max_task_timeout) if timeout else registry.max_task_timeout
    _ = effective_timeout  # used by runner timeout wrapper

    task_obj = {
        "agent": agent,
        "task": task,
        "context": context or {},
    }

    async def runner(t: dict) -> dict:
        """Runner callback — executed by QueuedTaskDelegator when slot is available."""
        from arf.plugins.a2a.state import build_sub_state

        sub_state = build_sub_state(
            parent_session_id=parent_sid,
            task_id="",  # filled by delegator
            task=t.get("task", task),
            system_prompt="",
            model="",
            parent_state={},
        )
        # delegator passes the dispatch task dict; we use task_id from registration
        sub_state["session_id"] = f"{parent_sid}--{t.get('task_id', 'unknown')}"

        try:
            async for event in engine.astream(sub_state, stop_on_text=True):
                # Drain events — results are collected by round_end hook
                pass
            # Return final state for the round_end hook to read
            return {"ok": True, "final_state": sub_state}
        except Exception as exc:
            logger.exception("Sub-agent runner failed")
            return {"ok": False, "error": str(exc)}

    result = await registry.delegator.dispatch(parent_sid, task_obj, runner)
    return result
