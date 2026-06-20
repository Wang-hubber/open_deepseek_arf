"""to_openai_tools — convert framework tool definitions to OpenAI API format."""
from __future__ import annotations


def to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    """Convert framework ToolDefinition list to OpenAI function-calling format.

    Returns None when tools is empty/None (API prefers absent key over empty list).
    """
    if not tools:
        return None
    result = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        params = t.get("parameters", {})
        result.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": params if params else {"type": "object", "properties": {}},
            },
        })
    return result if result else None
