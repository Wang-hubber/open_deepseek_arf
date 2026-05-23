"""Protocols for Record & Replay."""
from typing import Protocol, AsyncIterator
from dataclasses import dataclass, field
from arf.core.events import AgentEvent


@dataclass
class TurnRecord:
    turn: int
    model_name: str
    model_input: dict = field(default_factory=dict)
    model_output: str = ""
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class ReplayTrace:
    session_id: str
    agent_config_hash: str
    arf_version: str
    turns: list[TurnRecord] = field(default_factory=list)


class ReplayController(Protocol):
    async def start_recording(self, session_id: str) -> None: ...
    async def record_model_output(
        self, session_id: str, turn: int, model_name: str, output: str,
    ) -> None: ...
    async def record_tool_result(
        self, session_id: str, turn: int, tool_name: str, params: dict, result: dict,
    ) -> None: ...
    async def stop_recording(self) -> ReplayTrace: ...
    async def replay(
        self, trace: ReplayTrace, *, start_turn: int = 0,
        breakpoints: list[int] | None = None,
    ) -> AsyncIterator[AgentEvent]: ...
