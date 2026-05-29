"""Unified event model — single source for streaming + observability."""

from dataclasses import dataclass, field
from typing import Literal
import time

EventType = Literal[
    "session_start", "session_end",
    "user_input",
    "thinking_delta",
    "model_call_start", "model_call_end",
    "tool_call_start", "tool_call_end", "tool_call_result",  # tool_call_result — used by ReplayController
    "compaction_start", "compaction_end",
    "approval_required",
    "approval_resolved",
    "agent_switch",
    "guard_block",          # PathCheckToolGuard or ToolPermissionChecker blocked a tool
    "guard_pass",           # All guard checks passed for a tool
    "hook_start", "hook_end",
    "undo_executed",        # undo boundary marker — trace never deletes, only marks
    "rollback_executed",    # tool rollback completed (with rolled_back list)
    "error",
    # Protection (TODO #10)
    "rate_limited",         # TokenBucket refused — rate limit hit
    "circuit_opened",       # CircuitBreaker → OPEN (tripped)
    "circuit_half_open",    # CircuitBreaker → HALF_OPEN (probing)
    "circuit_closed",       # CircuitBreaker → CLOSED (recovered)
    "breaker_blocked",      # CircuitBreaker OPEN blocked a request
]

@dataclass
class AgentEvent:
    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str | None = None
    session_id: str = ""
    agent_name: str = ""
    turn: int = 0
