"""Serialize AgentEvent to JSON-compatible dict for SSE/WS transport."""
from __future__ import annotations

import json
from arf.core.events import AgentEvent


def event_to_dict(event: AgentEvent) -> dict:
    """Convert an AgentEvent dataclass to a JSON-serializable dict.

    Strips framework-internal params (_register_wait, _emit) from
    tool_call_start/tool_call_end data to avoid serialization errors.
    """
    data = dict(event.data) if event.data else {}

    _FRAMEWORK_PARAMS = {"_register_wait", "_emit"}
    if event.type in ("tool_call_start", "tool_call_end"):
        data = {k: v for k, v in data.items() if k not in _FRAMEWORK_PARAMS}

    # Ensure data is JSON-safe — convert non-serializable values
    clean_data = {}
    for k, v in data.items():
        try:
            json.dumps(v)
            clean_data[k] = v
        except (TypeError, ValueError):
            clean_data[k] = str(v)

    result = {
        "type": event.type,
        "data": clean_data,
        "session_id": event.session_id,
        "timestamp": event.timestamp,
    }
    if event.turn:
        result["turn"] = event.turn
    if event.agent_name:
        result["agent_name"] = event.agent_name
    if event.primitive:
        result["primitive"] = event.primitive
    if event.level:
        result["level"] = event.level
    return result
