"""Sub-agent state construction for A2A task delegation."""
from arf.core.state import AgentState


def build_sub_state(
    *,
    parent_session_id: str,
    task_id: str,
    task: str,
    system_prompt: str = "",
    model: str = "",
    parent_state: dict | None = None,
) -> AgentState:
    """Build initial state for a sub-agent spawned via delegate_task.

    session_id is {parent_session_id}--{task_id} for trace correlation.
    """
    sub_sid = f"{parent_session_id}--{task_id}"

    msgs: list[dict] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": task})

    if not model and parent_state:
        model = parent_state.get("current_model", "")

    return {
        "session_id": sub_sid,
        "messages": msgs,
        "current_model": model,
        "current_turn": 0,
        "interaction_round": 0,
        "parent_session_id": parent_session_id,
        "context_summary": "",
        "tool_results": {},
        "metadata": {},
        "session_active": True,
    }
