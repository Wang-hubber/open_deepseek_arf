"""Agent state types — message, wait, model result dataclasses."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    message_id: str
    role: str          # "system" | "user" | "assistant" | "tool"
    content: Any       # str for text, dict for structured data


@dataclass
class WaitItem:
    wait_id: str
    hook_name: str     # harness checkpoint name
    reason: str
    created_at: float = 0.0


@dataclass
class ModelResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)  # [{id, name, params}]
    usage: dict = field(default_factory=dict)
    finish_reason: str = "stop"
    reasoning_content: str = ""  # DeepSeek thinking mode reasoning


@dataclass
class AgentState:
    agent_id: str
    session_id: str
    messages: list[Message]
    waiting: dict[str, list[WaitItem]]   # hook_name -> [WaitItem, ...]
    model_config: dict                   # {api_base, api_key_env, model_name, context_window}
