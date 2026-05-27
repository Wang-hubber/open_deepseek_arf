"""events_to_trace — AgentEvent list → structured trace dict for metrics."""
from arf.core.events import AgentEvent


def events_to_trace(events: list[AgentEvent]) -> dict:
    """Convert a flat AgentEvent list into {turns: [{turn, tool_calls, model_output, error, duration_ms}]}."""
    turns: dict[int, dict] = {}

    for e in events:
        t = e.turn
        if t not in turns:
            turns[t] = {"turn": t, "tool_calls": [], "model_output": "", "error": None, "duration_ms": 0}

        if e.type == "tool_call_end":
            turns[t]["tool_calls"].append({
                "tool_name": e.data.get("tool_name", ""),
                "success": e.data.get("success", False),
                "error": e.data.get("error", ""),
            })
            turns[t]["duration_ms"] += e.data.get("duration_ms", 0)
        elif e.type == "model_call_end":
            turns[t]["model_output"] = e.data.get("content", "")
        elif e.type == "error":
            turns[t]["error"] = e.data.get("detail", "") or e.data.get("message", "")

    return {"turns": [turns[k] for k in sorted(turns)]}
