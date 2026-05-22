"""LangGraph AgentState schema for the ARF agent graph."""

from typing import Annotated, Optional, TypedDict


def reduce_messages(a: list, b: list) -> list:
    """Concatenate message lists without converting to LangChain objects.

    Unlike add_messages, this keeps messages as plain dicts matching
    ARF's native message format ({'role': ..., 'content': ...}).
    """
    return list(a) + list(b)


def reduce_usage(a: dict, b: dict) -> dict:
    """Accumulate token usage across turns."""
    result = dict(a)
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        result[k] = result.get(k, 0) + b.get(k, 0)
    return result


def reduce_tool_events(a: list, b: list) -> list:
    """Accumulate tool events across turns."""
    return list(a) + list(b)


def reduce_node_traces(a: list, b: list) -> list:
    """Accumulate node execution traces across turns."""
    return list(a) + list(b)


class AgentState(TypedDict):
    """State carried through the LangGraph agent graph.

    Holds conversation, control plane, model routing, trace accumulation,
    and classification metadata.
    """

    # Conversation -- plain list concatenation keeps ARF dict format
    messages: Annotated[list, reduce_messages]
    system_prompt: str
    tools: Optional[list[dict]]

    # Control plane
    turn_count: int
    max_turns: int
    # None = terminal (graph routes to respond). The frontend TraceView
    # depends on transition=None to detect Turn boundaries — a None value
    # means the current user/agent turn has completed. Do not repurpose
    # None semantics without updating frontend Turn grouping logic.
    transition: Optional[str]
    continuation_count: int
    compaction_count: int
    context_summary: Optional[str]
    stop_hook_active: bool
    _needs_tools_refresh: bool
    tool_fail_counts: dict[str, int]

    # Model routing
    current_model: str  # "quick_thinking" | "deep_thinking"
    agent_mode: Optional[str]  # "user" | "sys"
    classification: Optional[str]  # "medium" | "complex"
    reclassify_interval: int  # 0 = disabled, N = re-run classifier every N turns

    # Accumulators (custom reducers for cross-turn accumulation)
    usage: Annotated[dict, reduce_usage]
    tool_events: Annotated[list[dict], reduce_tool_events]
    node_traces: Annotated[list[dict], reduce_node_traces]

    # Output
    final_response: Optional[str]

    # Error handling
    last_error: Optional[str]
    truncated: bool

    # Token tracking — actual prompt_tokens from last API call, used by
    # compact_node as the baseline instead of character-based estimation.
    used_tokens: int


def default_state(
    messages: Optional[list[dict]] = None,
    system_prompt: str = "",
    tools: Optional[list[dict]] = None,
    max_turns: int = 10,
    current_model: str = "quick_thinking",
    reclassify_interval: int = 0,
) -> dict:
    """Build a minimal initial state dict for graph invocation."""
    return {
        "messages": list(messages) if messages else [],
        "system_prompt": system_prompt,
        "tools": tools,
        "turn_count": 1,
        "max_turns": max_turns,
        "transition": None,
        "continuation_count": 0,
        "compaction_count": 0,
        "context_summary": None,
        "stop_hook_active": False,
        "_needs_tools_refresh": False,
        "tool_fail_counts": {},
        "current_model": current_model,
        "agent_mode": "user",
        "classification": None,
        "reclassify_interval": reclassify_interval,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "tool_events": [],
        "node_traces": [],
        "final_response": None,
        "last_error": None,
        "truncated": False,
        "used_tokens": 0,
    }
