"""Standard result types shared across Protocols."""
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


@dataclass
class HookResult:
    hook_name: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    injected_message: str | None = None


@dataclass
class ApprovalRequest:
    agent_name: str
    session_id: str
    turn: int
    tool_name: str
    params: dict
    reason: str


@dataclass
class ApprovalResponse:
    action: Literal["approve", "reject", "modify"]
    modified_params: dict | None = None
    comment: str = ""


@dataclass
class ErrorAction:
    action: Literal["retry", "fallback", "ask_user", "abort"]
    delay: float = 0.0
    fallback_model: str | None = None
    message: str = ""


@dataclass
class RollbackResult:
    success: bool
    rollbacks: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    restored_state: dict = field(default_factory=dict)
