"""Unified event model — single source for streaming + observability."""

from dataclasses import dataclass, field
from typing import Literal
import time

EventType = Literal[
    "session_start", "session_end",
    "user_input",
    "thinking_delta",
    "model_call_start", "model_call_end",
    "tool_call_start", "tool_call_end",
    "compaction_start", "compaction_end",
    "approval_required",
    "approval_resolved",
    "guard_block",          # PathCheckToolGuard or ToolPermissionChecker blocked a tool
    "guard_pass",           # All guard checks passed for a tool
    "hook_start", "hook_end",
    "error",
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
