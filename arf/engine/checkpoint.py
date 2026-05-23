"""InMemoryStateStore — dict-backed checkpoint implementation."""
from arf.core.protocols import StateStore
from arf.core.state import AgentState


class InMemoryStateStore:
    def __init__(self) -> None:
        self._store: dict[str, AgentState] = {}
        self.snapshots: list[dict] = []   # for testing

    async def put(self, session_id: str, state: AgentState) -> None:
        import copy
        snapshot = copy.deepcopy(dict(state))
        self._store[session_id] = snapshot
        self.snapshots.append({"session_id": session_id, "turn": snapshot.get("current_turn", 0)})

    async def get(self, session_id: str) -> AgentState | None:
        return self._store.get(session_id)

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    def reset(self) -> None:
        self._store.clear()
        self.snapshots.clear()
