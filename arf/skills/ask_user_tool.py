"""ask_user — kernel tool for sub-agent human-in-the-loop.

Returns a structured result with pending=True. ControlPlane detects
this in _action_execute_tools and sets state["_pending_human_decision"].
The sub-agent then ends its round naturally; A2APlugin.round_end emits
human_decision_required event. Human answer is injected as a new user
message for the next round.
"""
import logging

logger = logging.getLogger("arf.skills.ask_user")


async def execute(
    question: str,
    options: list[str] | None = None,
    **kwargs,
) -> dict:
    return {
        "ok": True,
        "pending": True,
        "question": question,
        "options": options or [],
    }
