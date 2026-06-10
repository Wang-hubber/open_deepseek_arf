"""Protocols for engine domain."""
from typing import Protocol
from arf.core.state import AgentState, TurnContext
from arf.core.results import ToolResult, ErrorAction


class StateStore(Protocol):
    """Persist/restore AgentState at checkpoint boundaries."""
    async def put(self, session_id: str, state: AgentState) -> None: ...
    async def get(self, session_id: str) -> AgentState | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def list_sessions(self) -> list[str]: ...


class ToolExecutor(Protocol):
    """Execute multiple tool_calls with concurrency control."""
    async def execute(
        self,
        tool_calls: list[dict],
        strategy: str = "parallel",
        max_concurrency: int = 5,
    ) -> dict[str, ToolResult]: ...
