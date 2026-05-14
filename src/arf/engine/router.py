"""Conditional edge functions for the ARF agent graph."""

import logging
from .state import AgentState

logger = logging.getLogger("arf.graph.router")


def decide_entry(state: AgentState) -> str:
    """Determine entry point: classify on turn 1, otherwise go straight to model."""
    if state.get("turn_count", 1) <= 1 and state.get("classification") is None:
        return "classify"
    return "call_model"


def _over_max_turns(state: AgentState) -> bool:
    """Check if turn_count has exceeded max_turns."""
    return state.get("turn_count", 0) > state.get("max_turns", 10)


def decide_entry(state: AgentState) -> str:
    """Determine entry point: classify on turn 1, otherwise go straight to model."""
    if state.get("turn_count", 1) <= 1 and state.get("classification") is None:
        return "classify"
    return "call_model"


def route_after_model(state: AgentState) -> str:
    """Determine next node after call_model completes.

    Inspects the last message for tool_calls, checks for truncation,
    and routes to the appropriate next node.
    """
    # Check max_turns first -- turn_count was already incremented by call_model_node
    if _over_max_turns(state):
        return "respond"

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else {}

    # Tool calls -> execute tools
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
