"""ask_user — kernel tool for human-in-the-loop input requests.

Registers a wait on before_round and emits need_human_input event.
Engine injects _register_wait and _emit at before_tools (see Task 4).
"""
import logging

logger = logging.getLogger("arf.skills.ask_user")


async def execute(
    question: str,
    options: list[str] | None = None,
    context: str = "",
    task_id: str = "",
    _register_wait=None,
    _emit=None,
    **kwargs,
) -> dict:
    wi = None
    if _register_wait is not None:
        wi = _register_wait("before_round", "hitl", resume_key="")
    if _emit is not None:
        _emit("need_human_input", {
            "question": question,
            "options": options or [],
            "context": context,
            "task_id": task_id,
            "wait_id": wi.wait_id if wi else "",
        })
    return {
        "ok": True,
        "pending": True,
        "wait_id": wi.wait_id if wi else "",
        "question": question,
        "options": options or [],
        "context": context,
        "task_id": task_id,
    }
