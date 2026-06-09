"""Standard result types shared across Protocols."""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    modified_message: str | None = None


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    rollback: Callable | None = None
    rolled_back: bool = False
    rollback_error: str | None = None
    blocked: bool = False


@dataclass
class HookResult:
    hook_name: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    injected_message: str | None = None


@dataclass
class ErrorAction:
    action: Literal["retry", "fallback", "ask_user", "abort"]
    delay: float = 0.0
    fallback_model: str | None = None
    message: str = ""


@dataclass
class RecoveryState:
    """Per-session recovery budget tracking. Stored in AgentState['_recovery_state'].

    Each recovery path has its own counter — prevents infinite loops by
    enforcing independent retry budgets.
    """
    continuation_attempts: int = 0   # max_tokens 续写计数
    compact_attempts: int = 0        # context overflow 压缩计数
    transport_attempts: int = 0      # timeout/rate/connection 退避计数


@dataclass
class RecoveryDecision:
    """Output of choose_recovery: what action to take and why."""
    kind: Literal["continue", "compact", "backoff", "fail"]
    reason: str
