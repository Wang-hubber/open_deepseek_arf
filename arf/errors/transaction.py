from arf.core.results import RollbackResult


class SnapshotRollback:
    def __init__(self) -> None:
        self._snapshots: dict[str, dict] = {}

    async def begin(self, session_id: str, turn: int) -> dict:
        tx = {"id": f"{session_id}:{turn}", "session_id": session_id, "turn": turn,
              "state_snapshot": None, "tool_results": []}
        self._snapshots[tx["id"]] = tx
        return tx

    async def commit(self, tx: dict) -> None:
        self._snapshots.pop(tx["id"], None)

    async def rollback(self, tx: dict, error: Exception) -> RollbackResult:
        self._snapshots.pop(tx["id"], None)
        unresolved = []
        for tr in tx.get("tool_results", []):
            if not tr.get("rollback_fn"):
                unresolved.append(tr.get("tool_name", "unknown"))
        return RollbackResult(
            success=len(unresolved) == 0,
            unresolved=unresolved,
            restored_state=tx.get("state_snapshot", {}),
        )
