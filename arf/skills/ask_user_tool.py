"""ask_user — kernel tool for human-in-the-loop input requests.

Returns a structured result with pending=True. Engine detects this
in _detect_primitives, calls HITLProtocol.request_input() which emits
need_human_input event. The round ends. Human answer is injected as
a new user message for the next round.
"""
import logging

logger = logging.getLogger("arf.skills.ask_user")


async def execute(
    question: str,
    options: list[str] | None = None,
    context: str = "",
    task_id: str = "",
    **kwargs,
) -> dict:
    return {
        "ok": True,
        "pending": True,
        "question": question,
        "options": options or [],
        "context": context,
        "task_id": task_id,
    }
