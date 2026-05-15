"""Conditional edge functions for the ARF agent graph."""

import logging
from .state import AgentState

logger = logging.getLogger("arf.graph.router")


def decide_entry(state: AgentState) -> str:
    """Determine entry point: classify on turn 1, or when model change is requested."""
    if state.get("turn_count", 1) <= 1 and state.get("classification") is None:
        return "classify"

    # Re-classify when user explicitly requests model change or complexity shift
    messages = state.get("messages", [])
    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                last_user_msg = content[:500].lower()
            break

    if _requests_model_change(last_user_msg) or _suggests_complexity_shift(last_user_msg):
        return "classify"

    return "call_model"


def _requests_model_change(text: str) -> bool:
    """Detect explicit model switch requests."""
    triggers = [
        "切换到", "切到", "换成", "用深度", "用快速", "用慢速",
        "switch to", "use deep", "use quick", "change model",
        "深度思考", "deep thinking", "deep_thinking",
        "快速思考", "quick thinking", "quick_thinking",
    ]
    return any(t in text for t in triggers)


def _suggests_complexity_shift(text: str) -> bool:
    """Detect phrases that suggest a jump in task complexity."""
    indicators = [
        "架构", "重构", "设计", "插件", "系统", "多文件",
        "architecture", "refactor", "design", "plugin", "system",
        "从零", "from scratch", "大改动", "整体",
    ]
    return sum(1 for i in indicators if i in text) >= 2


def _over_max_turns(state: AgentState) -> bool:
    """Check if turn_count has exceeded max_turns."""
    return state.get("turn_count", 0) > state.get("max_turns", 10)


def route_after_model(state: AgentState) -> str:
    """Determine next node after call_model completes.

    Inspects the last message for tool_calls, checks for truncation,
    and routes to the appropriate next node.
    """
    # Check max_turns first — turn_count was already incremented by call_model_node
    if _over_max_turns(state):
        return "respond"

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else {}

    # Tool calls → execute tools
    if last_msg.get("tool_calls"):
        return "execute_tools"

    # Check for recovery scenarios
    transition = state.get("transition", "")
    if transition == "max_tokens_recovery":
        return "recovery"

    # Check for error state
    if state.get("last_error"):
        return "recovery"

    # Terminal
    return "respond"


def route_after_tools(state: AgentState) -> str:
    """After tool execution: check limits, then return to model."""
    if _over_max_turns(state) or state.get("truncated"):
        return "respond"
    return "call_model"


def should_continue(state: AgentState) -> str:
    """After recovery node: loop back to model or end."""
    if state.get("truncated") or _over_max_turns(state):
        return "stop"
    return "continue"
