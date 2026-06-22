"""Protocols for engine domain."""
from typing import Protocol
from arf.core.state import AgentState


class StateStore(Protocol):
    """Persist/restore AgentState at checkpoint boundaries."""
    async def put(self, session_id: str, state: AgentState) -> None: ...
    async def get(self, session_id: str) -> AgentState | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def list_sessions(self) -> list[str]: ...




