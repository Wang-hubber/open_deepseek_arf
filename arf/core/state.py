"""Core state types — engine read/write, StateStore persists."""

from dataclasses import dataclass, field
from typing import TypedDict


class AgentState(TypedDict, total=False):
    session_id: str
    agent_name: str
    messages: list[dict]
    current_model: str
    current_turn: int
    interaction_round: int       # user-interaction round (groups internal turns)
    context_summary: str
    tool_results: dict[str, dict]
    plan: dict | None
    metadata: dict


@dataclass
class TurnContext:
    session_id: str
    agent_name: str
    turn: int
    current_model: str
    available_models: list[str]
    last_user_message: str
    last_tool_calls: list[dict] = field(default_factory=list)
